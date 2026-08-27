param(
    [string]$CandidateCsv = "C:\Users\86166\Desktop\sukaSeafoodReview\collector\output\candidates.csv",
    [string]$SshHost = "dianshu-prod",
    [switch]$Commit,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$RemoteRoot = "/opt/sukaseafood-review"
$RemoteImports = "/opt/sukaseafood-review/imports/"
$SshOptions = @(
    "-o", "BatchMode=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ConnectTimeout=10"
)

if ($WhatIf) {
    Write-Output "WHATIF-NO-NETWORK: validate one .csv, hash it, upload as SHA256.csv, run dry-run, retrieve report, and commit only with -Commit."
    exit 0
}

function Invoke-Native {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE"
    }
}

$ResolvedCsv = (Resolve-Path -LiteralPath $CandidateCsv).Path
$CsvItem = Get-Item -LiteralPath $ResolvedCsv
if (-not $CsvItem.PSIsContainer -and $CsvItem.Extension.ToLowerInvariant() -eq ".csv") {
    if (($CsvItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Candidate CSV must not be a ReparsePoint"
    }
}
else {
    throw "CandidateCsv must be one regular .csv file"
}

$Sha256 = (Get-FileHash -LiteralPath $ResolvedCsv -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Sha256 -notmatch '^[0-9a-f]{64}$') { throw "Unable to compute canonical SHA256" }
$RemoteCsv = "$RemoteImports$Sha256.csv"
$RemoteReport = "$RemoteImports$Sha256.report.json"
$RemoteCommitReport = "$RemoteImports$Sha256.commit-report.json"
$RemoteTemporary = "/tmp/sukaseafood-import-$Sha256.csv"
$TaskTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$TempDirectory = Join-Path $TaskTempRoot ("sukaseafood-import-" + [guid]::NewGuid().ToString("N"))
$LocalReport = Join-Path $TempDirectory "$Sha256.report.json"
$LocalCommitReport = Join-Path $TempDirectory "$Sha256.commit-report.json"
New-Item -ItemType Directory -Path $TempDirectory | Out-Null

try {
    Invoke-Native "scp" @($SshOptions + @($ResolvedCsv, "${SshHost}:$RemoteTemporary"))
    $RemoteHashLine = (& ssh @SshOptions $SshHost "sha256sum -- '$RemoteTemporary'").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Remote SHA256 verification failed" }
    if (($RemoteHashLine -split '\s+')[0].ToLowerInvariant() -ne $Sha256) {
        throw "Uploaded CSV SHA256 mismatch"
    }
    $Install = "install -d -m 0700 '$RemoteImports' && install -m 0600 '$RemoteTemporary' '$RemoteCsv' && rm -f -- '$RemoteTemporary'"
    Invoke-Native "ssh" @($SshOptions + @($SshHost, $Install))

    $DryRun = "cd '$RemoteRoot' && docker compose --env-file deploy/.env -f docker-compose.production.yml run --rm review-api python -m app.commands.import_candidates '/imports/$Sha256.csv' --dry-run --json-report '/imports/$Sha256.report.json'"
    Invoke-Native "ssh" @($SshOptions + @($SshHost, $DryRun))
    Invoke-Native "scp" @($SshOptions + @("${SshHost}:$RemoteReport", $LocalReport))
    $Report = Get-Content -LiteralPath $LocalReport -Raw -Encoding UTF8 | ConvertFrom-Json
    $Report | Select-Object total, species_counts, source_counts, blocking_errors, invalid_species, invalid_licenses, invalid_sources, missing_urls, exact_duplicates, possible_url_duplicates | ConvertTo-Json -Depth 5

    if ($Report.blocking_errors -ne 0 -or -not $Report.can_commit) {
        throw "Dry-run contains blocking or invalid rows; commit is forbidden"
    }

    if ($Commit) {
        $CommitCommand = "cd '$RemoteRoot' && docker compose --env-file deploy/.env -f docker-compose.production.yml run --rm review-api python -m app.commands.import_candidates '/imports/$Sha256.csv' --commit --json-report '/imports/$Sha256.commit-report.json'"
        Invoke-Native "ssh" @($SshOptions + @($SshHost, $CommitCommand))
        Invoke-Native "scp" @($SshOptions + @("${SshHost}:$RemoteCommitReport", $LocalCommitReport))
        $CommitReport = Get-Content -LiteralPath $LocalCommitReport -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($CommitReport.file_sha256 -ne $Sha256) {
            throw "Commit report SHA256 does not match the local CSV"
        }
        $CommitReport | Select-Object total, inserted, skipped_exact, possible_url_duplicates | ConvertTo-Json
        Write-Output "Committed the validated candidate CSV; report: $RemoteCommitReport"
    }
    else {
        Write-Output "Dry-run passed. Re-run with -Commit for the explicit import step."
    }
}
finally {
    $ResolvedTemp = [IO.Path]::GetFullPath($TempDirectory)
    if ($ResolvedTemp.StartsWith($TaskTempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $ResolvedTemp)) {
        Remove-Item -LiteralPath $ResolvedTemp -Recurse -Force
    }
}
