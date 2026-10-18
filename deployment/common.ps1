Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ApplicationId = 'enterprise-xml-analyzer'

function Assert-NoReparsePath([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if ((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Reparse points are not permitted in paths or their parents.' }
        $current = Split-Path -Parent $current
    }
}

function Assert-NoReparseTree([string]$Root) {
    Assert-NoReparsePath $Root
    if (Test-Path -LiteralPath $Root -PathType Container) {
        foreach ($item in Get-ChildItem -LiteralPath $Root -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point found inside target.' }
            if ($item.PSIsContainer) { Assert-NoReparseTree $item.FullName }
        }
    }
}

function Get-ValidatedPackage([string]$Root) {
    $full = [IO.Path]::GetFullPath($Root)
    $manifestPath = Join-Path $full 'deployment-manifest.json'
    Assert-NoReparsePath $manifestPath
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Package manifest is missing.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.appId -ne $ApplicationId -or $manifest.files.Count -lt 3) { throw 'Package identity is invalid.' }
    foreach ($entry in $manifest.files) {
        if ([IO.Path]::IsPathRooted($entry.path) -or $entry.path -match '(^|[/\\])\.\.([/\\]|$)' -or $entry.path -match ':') { throw 'Unsafe package path.' }
        $path = [IO.Path]::GetFullPath((Join-Path $full $entry.path))
        Assert-NoReparsePath $path
        if (-not $path.StartsWith($full.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Path escapes package root.' }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Package file missing: $($entry.path)" }
        if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse points are not permitted.' }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) { throw "Checksum mismatch: $($entry.path)" }
    }
    if (-not ($manifest.files.path -contains 'index.html') -or -not ($manifest.files.path -contains 'web.config')) { throw 'Required entry points are missing.' }
    return $manifest
}

function Assert-SafeTarget([string]$Target) {
    $full = [IO.Path]::GetFullPath($Target).TrimEnd('\')
    Assert-NoReparseTree $full
    if ($full -eq [IO.Path]::GetPathRoot($full).TrimEnd('\')) { throw 'Drive root cannot be a deployment target.' }
    if (Test-Path -LiteralPath $full) {
        $item = Get-Item -LiteralPath $full
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Target cannot be a reparse point.' }
        if (@(Get-ChildItem -LiteralPath $full -Force).Count -gt 0) {
            if (-not (Test-Path -LiteralPath (Join-Path $full 'deployment-manifest.json'))) { throw 'Nonempty target is not owned by this application. Use a dedicated empty directory.' }
            $owned = Get-Content -LiteralPath (Join-Path $full 'deployment-manifest.json') -Raw | ConvertFrom-Json
            if ($owned.appId -ne $ApplicationId) { throw 'Target belongs to another application.' }
        }
    }
    return $full
}

function Copy-PackageFiles([string]$Source, [string]$Target, $Manifest) {
    foreach ($entry in $Manifest.files) {
        $dest = Join-Path $Target $entry.path
        Assert-NoReparsePath $dest
        New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $Source $entry.path) -Destination $dest -Force
    }
    # Copy the manifest last; a failed deployment must not be reported as a new complete release.
    Copy-Item -LiteralPath (Join-Path $Source 'deployment-manifest.json') -Destination $Target -Force
}
