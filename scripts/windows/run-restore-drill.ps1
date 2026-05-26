param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$MysqlPath = "C:\xampp\mysql\bin\mysql.exe",
    [string]$Host = "127.0.0.1",
    [int]$Port = 3306,
    [string]$Username = "barangay_user",
    [string]$Password = "barangay_password",
    [string]$Database = "barangay_db"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $MysqlPath)) {
    throw "mysql executable not found: $MysqlPath"
}

if (-not (Test-Path $BackupPath)) {
    throw "Backup file not found: $BackupPath"
}

$env:MYSQL_PWD = $Password
try {
    Get-Content $BackupPath | & $MysqlPath --host=$Host --port=$Port --user=$Username $Database
} finally {
    Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
}

Write-Host "Restore drill completed using: $BackupPath"
