@echo off
setlocal

cd /d %~dp0

rem Navigate to project root (two levels up from packaging\windows_bundle\)
cd ..\..

if not exist "scripts\windows\install-service.ps1" (
  echo [ERROR] scripts\windows\install-service.ps1 not found.
  echo Make sure you are running this from packaging\windows_bundle\
  exit /b 1
)

echo Installing Barangay System as a Windows service via NSSM...
echo.

powershell -ExecutionPolicy Bypass -File "scripts\windows\install-service.ps1" -AppDir "%cd%"

echo.
echo Service install command finished.
endlocal
