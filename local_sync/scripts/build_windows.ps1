[CmdletBinding()]
param(
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptFile = [IO.Path]::GetFullPath($MyInvocation.MyCommand.Path)
$localSyncRoot = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $scriptFile) ".."))
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $localSyncRoot ".."))

function Assert-TaskLocalPath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$RequiredParent
    )
    $fullCandidate = [IO.Path]::GetFullPath($Candidate)
    $fullParent = [IO.Path]::GetFullPath($RequiredParent).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $prefix = $fullParent + [IO.Path]::DirectorySeparatorChar
    if (
        $fullCandidate.Equals($fullParent, [StringComparison]::OrdinalIgnoreCase) -or
        -not $fullCandidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing a path outside the task-local build root."
    }
    return $fullCandidate
}

function Assert-NoReparseAncestry {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$RequiredParent
    )
    $safePath = Assert-TaskLocalPath -Candidate $Candidate -RequiredParent $RequiredParent
    $boundary = [IO.Path]::GetFullPath($RequiredParent).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $current = Get-Item -LiteralPath $safePath -Force
    while ($true) {
        if (($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing a path with a reparse point in its task-local ancestry."
        }
        $currentPath = [IO.Path]::GetFullPath($current.FullName).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
        if ($currentPath.Equals($boundary, [StringComparison]::OrdinalIgnoreCase)) {
            return
        }
        $parentPath = Split-Path -Parent $currentPath
        if ([string]::IsNullOrWhiteSpace($parentPath)) {
            throw "Task-local path ancestry ended before the required parent."
        }
        $current = Get-Item -LiteralPath $parentPath -Force
    }
}

function Remove-VerifiedTree {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$RequiredParent
    )
    $safePath = Assert-TaskLocalPath -Candidate $Candidate -RequiredParent $RequiredParent
    if (-not (Test-Path -LiteralPath $safePath)) {
        return
    }
    Assert-NoReparseAncestry -Candidate $safePath -RequiredParent $RequiredParent
    $item = Get-Item -LiteralPath $safePath -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to recursively remove a reparse point."
    }
    $resolved = [IO.Path]::GetFullPath($item.FullName)
    if (-not $resolved.Equals($safePath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to recursively remove a path that changed during validation."
    }
    Remove-Item -LiteralPath $safePath -Recurse -Force
}

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

Assert-PackageBoundary -BundleRoot $bundlePath
$hash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant()
$hashPath = Join-Path $distPath "SHA256SUMS.txt"
$hashLine = "$hash  *SukaSeafoodTrainingSync/SukaSeafoodTrainingSync.exe`n"
[IO.File]::WriteAllText($hashPath, $hashLine, [Text.UTF8Encoding]::new($false))
Write-Host "Windows build verified: $exePath"
Write-Host "SHA256 manifest: $hashPath"
