@echo off
setlocal

cd /d %~dp0

if not exist ".env" (
  echo [INFO] .env not found. Copying from .env.example...
  copy /Y ".env.example" ".env" >nul
  echo [INFO] Please edit .env before first production use.
)

if not exist "backups" mkdir "backups"
if not exist "uploads" mkdir "uploads"

set APP_ENV=production
set HOST=0.0.0.0
set PORT=5000

echo Starting Barangay Server on http://%COMPUTERNAME%:%PORT%
echo Press Ctrl+C to stop.
echo.

barangay_server.exe

endlocal
