@echo off
setlocal

cd /d %~dp0

rem Navigate to project root (two levels up from packaging\windows_bundle\)
cd ..\..

if not exist "scripts\windows\setup.ps1" (
  echo [ERROR] scripts\windows\setup.ps1 not found.
  echo Make sure you are running this from packaging\windows_bundle\
  pause
  exit /b 1
)

rem Auto-elevate to administrator if needed
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo Requesting administrator privileges...
  powershell -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\setup.ps1"

endlocal
