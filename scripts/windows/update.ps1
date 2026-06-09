# Barangay System – One-Click Update (Windows)
# Run from the project root, or double-click  packaging\windows_bundle\UPDATE.bat

param(
    [string]$ServiceName = "BarangaySystem",
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir     = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$BundleDir  = Join-Path $AppDir "packaging\windows_bundle"
$DataDir    = Join-Path $AppDir "data"
$NssmBin    = Join-Path $BundleDir "nssm.exe"

Set-Location $AppDir
Write-Host "Barangay System Update" -ForegroundColor Cyan
Write-Host "App directory: $AppDir"

if (-not (Test-Path $NssmBin)) {
    Write-Host "ERROR: nssm.exe not found at $NssmBin" -ForegroundColor Red
    pause
    exit 1
}

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
