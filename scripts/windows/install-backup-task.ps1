param(
    [string]$BackupDir = "C:\barangay_system\data\backups",
    [string]$TaskName = "BarangayDailyBackup",
    [string]$RunAt = "18:00",
    [string]$SourceDb = "C:\barangay_system\data\barangay.db"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

$command = "`$ts = Get-Date -Format yyyyMMdd_HHmmss; Copy-Item '$SourceDb' '$BackupDir\barangay_`$ts.db'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -Command $command"
$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Scheduled task '$TaskName' created. Runs daily at $RunAt and copies the database file to $BackupDir."
