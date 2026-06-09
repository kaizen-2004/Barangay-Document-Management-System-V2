param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$TargetDb = "C:\barangay_system\data\barangay.db",
    [string]$ServiceName = "BarangaySystem"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupPath)) {
    throw "Backup file not found: $BackupPath"
}

Write-Host "WARNING: This will replace the live database with the backup file."
Write-Host "  Backup:  $BackupPath"
Write-Host "  Target:  $TargetDb"
Write-Host ""

$confirm = Read-Host "Type YES to continue"
if ($confirm -ne "YES") {
    Write-Host "Aborted."
    exit 0
}

$nssmBin = Join-Path (Split-Path -Parent (Get-Command nssm -ErrorAction SilentlyContinue).Source) ""
if (-not $nssmBin) {
    $nssmBin = "C:\barangay_system\data\nssm.exe"
}

# Stop service, restore, restart
Write-Host "Stopping service..."
if (Test-Path $nssmBin) {
    & $nssmBin stop $ServiceName
} else {
    sc.exe stop $ServiceName
}

Start-Sleep -Seconds 2

Write-Host "Restoring backup..."
Copy-Item $BackupPath $TargetDb -Force

Write-Host "Starting service..."
if (Test-Path $nssmBin) {
    & $nssmBin start $ServiceName
} else {
    sc.exe start $ServiceName
}

Write-Host "Restore complete using: $BackupPath"
