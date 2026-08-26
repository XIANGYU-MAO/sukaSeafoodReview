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

function Assert-SafeTreeEntry {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$TreeRoot,
        [Parameter(Mandatory = $true)][string]$RequiredParent
    )
    $safeEntry = Assert-TaskLocalPath -Candidate $Candidate -RequiredParent $RequiredParent
    $safeTree = Assert-TaskLocalPath -Candidate $TreeRoot -RequiredParent $RequiredParent
    Assert-NoReparseAncestry -Candidate $safeEntry -RequiredParent $RequiredParent
    $treePrefix = $safeTree.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    if (
        -not $safeEntry.Equals($safeTree, [StringComparison]::OrdinalIgnoreCase) -and
        -not $safeEntry.StartsWith($treePrefix, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing a cleanup entry outside the verified tree."
    }
    $item = Get-Item -LiteralPath $safeEntry -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing a reparse point inside the cleanup tree."
    }
    $observed = [IO.Path]::GetFullPath($item.FullName)
    if (-not $observed.Equals($safeEntry, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing a cleanup entry that changed during validation."
    }
    return $item
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

    # Preflight the whole tree without asking PowerShell to recurse. Child
    # reparse points are rejected before any content is removed.
    $directories = [Collections.Generic.List[string]]::new()
    $files = [Collections.Generic.List[string]]::new()
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($safePath)
    while ($pending.Count -gt 0) {
        $currentPath = $pending.Pop()
        $current = Assert-SafeTreeEntry -Candidate $currentPath -TreeRoot $safePath -RequiredParent $RequiredParent
        if (-not $current.PSIsContainer) {
            throw "Cleanup tree root must be a directory."
        }
        $directories.Add($currentPath)
        foreach ($child in @(Get-ChildItem -LiteralPath $currentPath -Force)) {
            $childPath = [IO.Path]::GetFullPath($child.FullName)
            $verified = Assert-SafeTreeEntry -Candidate $childPath -TreeRoot $safePath -RequiredParent $RequiredParent
            if ($verified.PSIsContainer) {
                $pending.Push($childPath)
            }
            else {
                $files.Add($childPath)
            }
        }
    }

    # Re-inspect every entry during bottom-up removal. Directories are removed
    # non-recursively so a late junction can never be followed outside.
    foreach ($filePath in $files) {
        $file = Assert-SafeTreeEntry -Candidate $filePath -TreeRoot $safePath -RequiredParent $RequiredParent
        if ($file.PSIsContainer) {
            throw "Cleanup entry changed type during removal."
        }
        Remove-Item -LiteralPath $filePath -Force
    }
    foreach ($directoryPath in @($directories | Sort-Object { $_.Length } -Descending)) {
        $directory = Assert-SafeTreeEntry -Candidate $directoryPath -TreeRoot $safePath -RequiredParent $RequiredParent
        if (-not $directory.PSIsContainer) {
            throw "Cleanup entry changed type during removal."
        }
        if (@(Get-ChildItem -LiteralPath $directoryPath -Force).Count -ne 0) {
            throw "Cleanup tree changed during removal."
        }
        Remove-Item -LiteralPath $directoryPath -Force
    }
}
