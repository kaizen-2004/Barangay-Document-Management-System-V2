param(
    [string]$MysqlDumpPath = "C:\xampp\mysql\bin\mysqldump.exe",
    [string]$Host = "127.0.0.1",
    [int]$Port = 3306,
    [string]$Username = "barangay_user",
    [string]$Password = "barangay_password",
    [string]$Database = "barangay_db",
    [string]$BackupDir = "C:\barangay_system\backups",
    [string]$TaskName = "BarangayDailyBackup",
    [string]$RunAt = "18:00"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $MysqlDumpPath)) {
    throw "mysqldump not found: $MysqlDumpPath"
}

if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

$command = "`$env:MYSQL_PWD='$Password'; `$ts = Get-Date -Format yyyyMMdd_HHmmss; & '$MysqlDumpPath' --host=$Host --port=$Port --user=$Username --single-transaction --quick $Database > '$BackupDir\backup_`$ts.sql'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -Command $command"
$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Scheduled task '$TaskName' created. Runs daily at $RunAt and dumps MySQL database '$Database' to $BackupDir."
