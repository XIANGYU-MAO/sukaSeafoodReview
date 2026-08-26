param(
    [string]$SshHost = "dianshu-prod",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$RemoteRoot = "/opt/sukaseafood-review"
$SshOptions = @(
    "-o", "BatchMode=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ConnectTimeout=10"
)

if ($WhatIf) {
    Write-Output "WHATIF-NO-NETWORK: archive Git HEAD; verify SHA-256; stage explicitly; preserve deploy/.env, backups, and imports; run deploy_cloud.sh."
    exit 0
}

function Invoke-Native {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE"
    }
}

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "../..")).Path
$TaskTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$TempDirectory = Join-Path $TaskTempRoot ("sukaseafood-review-deploy-" + [guid]::NewGuid().ToString("N"))
$Archive = Join-Path $TempDirectory "review.tar"

New-Item -ItemType Directory -Path $TempDirectory | Out-Null
try {
    Push-Location $RepoRoot
    try {
        $Revision = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0 -or $Revision -notmatch '^[0-9a-f]{40}$') {
            throw "Unable to resolve a canonical Git HEAD"
        }
        Invoke-Native "git" @(
            "archive", "--format=tar", "--output=$Archive", $Revision, "--",
            "api", "web", "local_sync", "deploy",
            "docker-compose.yml", "docker-compose.production.yml"
        )
    }
    finally {
        Pop-Location
    }

    $LocalSha = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    $RemoteArchive = "/tmp/sukaseafood-review-$Revision.tar"
    $RemoteStage = "/tmp/sukaseafood-review-stage-$Revision"
    Invoke-Native "scp" @($SshOptions + @($Archive, "${SshHost}:$RemoteArchive"))
    $RemoteHashLine = (& ssh @SshOptions $SshHost "sha256sum -- '$RemoteArchive'").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Remote SHA-256 command failed" }
    $RemoteSha = ($RemoteHashLine -split '\s+')[0].ToLowerInvariant()
    if ($RemoteSha -ne $LocalSha) { throw "Uploaded archive SHA-256 mismatch" }

    $RemoteCommand = @"
set -Eeuo pipefail
case '$RemoteStage' in
    /tmp/sukaseafood-review-stage-$Revision) ;;
    *) echo 'refusing unsafe review stage path' >&2; exit 2 ;;
esac
if ! test ! -e '$RemoteStage'; then
    rm -f -- '$RemoteArchive'
    echo 'review stage already exists; inspect it before retrying' >&2
    exit 2
fi
install -d -m 0700 '$RemoteStage'
cleanup_review_stage() {
    rm -f -- '$RemoteArchive'
    if test -d '$RemoteStage'; then
        find '$RemoteStage' -xdev -depth -delete
    fi
}
trap cleanup_review_stage EXIT HUP INT TERM
tar -xf '$RemoteArchive' -C '$RemoteStage'
install -d -m 0700 '$RemoteRoot' '$RemoteRoot/backups' '$RemoteRoot/imports'
rsync -a --delete --exclude=deploy/.env --exclude=backups --exclude=imports '$RemoteStage/' '$RemoteRoot/'
chmod 700 '$RemoteRoot/deploy/scripts/'*.sh
'$RemoteRoot/deploy/scripts/deploy_cloud.sh' '$Revision'
cleanup_review_stage
trap - EXIT HUP INT TERM
test ! -e '$RemoteStage'
"@
    Invoke-Native "ssh" @($SshOptions + @($SshHost, $RemoteCommand))
    Write-Output "Review revision $Revision deployed and verified."
}
finally {
    $ResolvedTemp = [IO.Path]::GetFullPath($TempDirectory)
    if ($ResolvedTemp.StartsWith($TaskTempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $ResolvedTemp)) {
        Remove-Item -LiteralPath $ResolvedTemp -Recurse -Force
    }
}
