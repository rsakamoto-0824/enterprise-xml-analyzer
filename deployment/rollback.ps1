[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Backup, [Parameter(Mandatory=$true)][string]$Destination, [Parameter(Mandatory=$true)][string]$SafetyBackupRoot, [switch]$DryRun)
. (Join-Path $PSScriptRoot 'common.ps1')
$manifest = Get-ValidatedPackage $Backup
$target = Assert-SafeTarget $Destination
$safety = [IO.Path]::GetFullPath($SafetyBackupRoot).TrimEnd('\')
Assert-NoReparsePath $safety
$backupFull = [IO.Path]::GetFullPath($Backup).TrimEnd('\')
if ($backupFull -eq $target -or $backupFull.StartsWith($target+'\',[StringComparison]::OrdinalIgnoreCase) -or $target.StartsWith($backupFull+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Backup and target must be separate directories.' }
if ($safety -eq $target -or $safety.StartsWith($target+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Safety backup must be outside published directory.' }
if ($DryRun) { Write-Output "DryRun: preserve current files under $safety and restore verified backup to $target. No files changed."; exit 0 }
$snapshot = Join-Path $safety ((Get-Date).ToString('yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $snapshot -Force | Out-Null
Start-Transcript -Path (Join-Path $snapshot 'rollback.log') | Out-Null
try {
    # Preserve even a partly copied deployment. Its old checksums can legitimately fail.
    if (Test-Path -LiteralPath $target) {
        foreach ($item in Get-ChildItem -LiteralPath $target -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point found in target.' }
            Copy-Item -LiteralPath $item.FullName -Destination $snapshot -Recurse -Force
        }
    }
    Copy-PackageFiles $Backup $target $manifest
    Get-ValidatedPackage $target | Out-Null
    Write-Output "Rollback completed. Safety copy: $snapshot"
} finally { Stop-Transcript | Out-Null }
