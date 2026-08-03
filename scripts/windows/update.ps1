# Barangay System – One-Click Update (Windows)
# Run from the project root after replacing the source files.

param(
    [string]$ServiceName = "BarangaySystem",
    [int]$Port = 5000,
    [string]$NssmPath = "nssm.exe"
)

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir     = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$NssmCommand = Get-Command $NssmPath -ErrorAction SilentlyContinue

Set-Location $AppDir
Write-Host "Barangay System Update" -ForegroundColor Cyan
Write-Host "App directory: $AppDir"

if (-not $NssmCommand) {
    Write-Host "ERROR: NSSM was not found. Install NSSM and add nssm.exe to PATH." -ForegroundColor Red
    pause
    exit 1
}
$NssmBin = $NssmCommand.Source

Write-Host "Updating dependencies..."
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: uv sync failed" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "  ok: dependencies updated" -ForegroundColor Green

Write-Host "Restarting service..."
& $NssmBin restart $ServiceName
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: service restart returned code $LASTEXITCODE" -ForegroundColor Yellow
} else {
    Write-Host "  ok: service restarted" -ForegroundColor Green
}

Write-Host ""
Write-Host "Update complete. System is running on  http://localhost:$Port" -ForegroundColor Green
Start-Process "http://localhost:$Port"
pause
