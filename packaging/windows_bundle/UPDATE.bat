@echo off
setlocal

cd /d %~dp0

rem Navigate to project root (two levels up from packaging\windows_bundle\)
cd ..\..

if not exist "scripts\windows\update.ps1" (
  echo [ERROR] scripts\windows\update.ps1 not found.
  echo Make sure you are running this from packaging\windows_bundle\
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\update.ps1"

endlocal
