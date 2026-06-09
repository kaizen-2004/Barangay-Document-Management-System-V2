@echo off
setlocal

cd /d %~dp0

rem Navigate to project root (two levels up from packaging\windows_bundle\)
cd ..\..

if not exist ".env" (
  if exist ".env.example" (
    echo [INFO] .env not found. Copying from .env.example...
    copy /Y ".env.example" ".env" >nul
    echo [INFO] Please edit .env before first production use.
  ) else (
    echo [ERROR] Neither .env nor .env.example found.
    exit /b 1
  )
)

set HOST=0.0.0.0
set PORT=5000

echo Starting Barangay Server on http://%COMPUTERNAME%:%PORT%
echo Press Ctrl+C to stop.
echo.

uv run waitress-serve --host=%HOST% --port=%PORT% wsgi:app

endlocal
