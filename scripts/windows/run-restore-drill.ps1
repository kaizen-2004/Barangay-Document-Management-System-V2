param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$AppDir = "C:\barangay_system\app"
)

$ErrorActionPreference = "Stop"

$uvCmd = (Get-Command uv -ErrorAction SilentlyContinue)
if (-not $uvCmd) {
    throw "uv is not installed or not in PATH. Install uv first."
}

if (-not (Test-Path $AppDir)) {
    throw "App directory not found: $AppDir"
}

if (-not (Test-Path $BackupPath)) {
    throw "Backup file not found: $BackupPath"
}

Push-Location $AppDir
try {
    & $uvCmd.Source run flask --app wsgi restore-db --path $BackupPath --yes
} finally {
    Pop-Location
}

Write-Host "Restore drill completed using: $BackupPath"
