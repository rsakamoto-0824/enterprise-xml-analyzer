[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$BackupRoot,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'common.ps1')
$sourceFull = [IO.Path]::GetFullPath($Source).TrimEnd('\')
$target = Assert-SafeTarget $Destination
$backupFull = [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\')
Assert-NoReparsePath $backupFull
$manifest = Get-ValidatedPackage $sourceFull
if ($target -eq $sourceFull -or $sourceFull.StartsWith($target+'\',[StringComparison]::OrdinalIgnoreCase) -or $target.StartsWith($sourceFull+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Source and target must be separate directories.' }
if ($backupFull -eq $target -or $backupFull.StartsWith($target+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Backups must be outside the published directory.' }
if ($DryRun) { Write-Output "DryRun: validate $($manifest.files.Count) files; back up $target under $backupFull; copy version $($manifest.version). No files changed."; exit 0 }
New-Item -ItemType Directory -Path $backupFull -Force | Out-Null
$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss-fff')
$log = Join-Path $backupFull ("deploy-$stamp.log")
Start-Transcript -Path $log | Out-Null
try {
    if (Test-Path -LiteralPath (Join-Path $target 'deployment-manifest.json')) {
        $old = Get-ValidatedPackage $target
        $backup = Join-Path $backupFull $stamp
        New-Item -ItemType Directory -Path $backup | Out-Null
        Copy-PackageFiles $target $backup $old
        Get-ValidatedPackage $backup | Out-Null
        Write-Output "Verified backup: $backup"
    }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Copy-PackageFiles $sourceFull $target $manifest
    Get-ValidatedPackage $target | Out-Null
    Write-Output "Deployed version $($manifest.version). Run verify-deployment.ps1 and the Edge acceptance checklist. IIS was not restarted."
} finally { Stop-Transcript | Out-Null }
