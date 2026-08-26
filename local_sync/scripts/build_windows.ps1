[CmdletBinding()]
param(
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptFile = [IO.Path]::GetFullPath($MyInvocation.MyCommand.Path)
$localSyncRoot = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $scriptFile) ".."))
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $localSyncRoot ".."))

. (Join-Path (Split-Path -Parent $scriptFile) "build_helpers.ps1")

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "A build command failed with exit code $LASTEXITCODE."
    }
}

function Assert-PackageBoundary {
    param([Parameter(Mandatory = $true)][string]$BundleRoot)
    $safeBundle = Assert-TaskLocalPath -Candidate $BundleRoot -RequiredParent $localSyncRoot
    Assert-NoReparseAncestry -Candidate $safeBundle -RequiredParent $localSyncRoot
    $forbiddenNamePattern = '(?i)(^|[\\/])(tests?|fixtures)([\\/]|$)|(^|[\\/])\.env($|\.)|(^|[\\/])direct_url\.json$|\.csv$|\.sqlite(?:3)?(?:-|$)|\.jsonl$|(^|[\\/])test_[^\\/]*'
    $forbiddenMarkers = @(
        "test-only_batch-token_1234567890ABCDE",
        "images.example.test",
        "catalog.example.test",
        "tests/fixtures",
        "tests\fixtures",
        "not_an_image.txt"
    )
    $ascii = [Text.Encoding]::ASCII
    $unicode = [Text.Encoding]::Unicode
    $bundlePrefix = $safeBundle.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    $bundleEntries = Get-ChildItem -LiteralPath $safeBundle -Recurse -Force
    if ($bundleEntries | Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 }) {
        throw "Packaged content boundary rejected a reparse point."
    }
    foreach ($file in $bundleEntries | Where-Object { -not $_.PSIsContainer }) {
        $fullFile = [IO.Path]::GetFullPath($file.FullName)
        if (-not $fullFile.StartsWith($bundlePrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Packaged content boundary found a file outside the bundle root."
        }
        $relative = $fullFile.Substring($bundlePrefix.Length)
        if ($relative -match $forbiddenNamePattern) {
            throw "Packaged content boundary rejected a forbidden file name."
        }
        $bytes = [IO.File]::ReadAllBytes($file.FullName)
        $asciiText = $ascii.GetString($bytes)
        $unicodeText = $unicode.GetString($bytes)
        foreach ($marker in $forbiddenMarkers) {
            if ($asciiText.Contains($marker) -or $unicodeText.Contains($marker)) {
                throw "Packaged content boundary rejected forbidden fixture data."
            }
        }
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $pythonCommand = Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $PythonExecutable = $pythonCommand.Source
}
$PythonExecutable = [IO.Path]::GetFullPath($PythonExecutable)
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable was not found."
}

$version = & $PythonExecutable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne "3.12") {
    throw "This build requires Python 3.12."
}

$venvPath = Assert-TaskLocalPath -Candidate (Join-Path $localSyncRoot ".build\windows-venv") -RequiredParent $localSyncRoot
$workPath = Assert-TaskLocalPath -Candidate (Join-Path $localSyncRoot "build") -RequiredParent $localSyncRoot
$distPath = Assert-TaskLocalPath -Candidate (Join-Path $localSyncRoot "dist") -RequiredParent $localSyncRoot
$lockPath = Join-Path $localSyncRoot "requirements-build.lock"
$specPath = Join-Path $localSyncRoot "packaging\suka-seafood-sync.spec"

Remove-VerifiedTree -Candidate $venvPath -RequiredParent $localSyncRoot
Remove-VerifiedTree -Candidate $workPath -RequiredParent $localSyncRoot
Remove-VerifiedTree -Candidate $distPath -RequiredParent $localSyncRoot
New-Item -ItemType Directory -Path (Split-Path -Parent $venvPath) -Force | Out-Null

Invoke-Checked -Executable $PythonExecutable -Arguments @("-m", "venv", $venvPath)
$venvPython = Join-Path $venvPath "Scripts\python.exe"
Invoke-Checked -Executable $venvPython -Arguments @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", "-r", $lockPath
)
Invoke-Checked -Executable $venvPython -Arguments @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", "--no-build-isolation", $localSyncRoot
)
Invoke-Checked -Executable $venvPython -Arguments @("-m", "pip", "check")

Push-Location $localSyncRoot
try {
    Invoke-Checked -Executable $venvPython -Arguments @("-m", "pytest", "-q")
    Invoke-Checked -Executable $venvPython -Arguments @(
        "-m", "PyInstaller", "--clean", "--noconfirm",
        "--distpath", $distPath, "--workpath", $workPath, $specPath
    )
}
finally {
    Pop-Location
}

$bundlePath = Join-Path $distPath "SukaSeafoodTrainingSync"
$exePath = Join-Path $bundlePath "SukaSeafoodTrainingSync.exe"
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "The exact packaged executable was not produced."
}
$versionOutput = & $exePath --version
if ($LASTEXITCODE -ne 0 -or $versionOutput.Trim() -ne "0.1.0") {
    throw "The frozen --version smoke test failed."
}
$selfTestOutput = & $exePath self-test
if ($LASTEXITCODE -ne 0 -or $selfTestOutput.Trim() -ne "SELF-TEST OK") {
    throw "The frozen functional self-test failed."
}

$archiveListing = & $venvPython -m PyInstaller.utils.cliutils.archive_viewer -r -b $exePath
if ($LASTEXITCODE -ne 0) {
    throw "The frozen module boundary inspection failed."
}
$forbiddenFrozenModule = '(^|\.)(tests?|fixtures)(\.|$)|(^|\.)(_pytesttester|_testutils)$|(^|\.)(pytest|unittest)(\.|$)'
foreach ($archiveLine in $archiveListing) {
    if ($archiveLine.Trim() -match $forbiddenFrozenModule) {
        throw "Packaged content boundary rejected a forbidden frozen module."
    }
}

Assert-PackageBoundary -BundleRoot $bundlePath
$hash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant()
$hashPath = Join-Path $distPath "SHA256SUMS.txt"
$hashLine = "$hash  *SukaSeafoodTrainingSync/SukaSeafoodTrainingSync.exe`n"
[IO.File]::WriteAllText($hashPath, $hashLine, [Text.UTF8Encoding]::new($false))
Write-Host "Windows build verified: $exePath"
Write-Host "SHA256 manifest: $hashPath"
