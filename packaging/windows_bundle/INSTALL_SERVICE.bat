@echo off
setlocal

cd /d %~dp0

if not exist "windows-scripts\install-service.ps1" (
  echo [ERROR] windows-scripts\install-service.ps1 not found.
  exit /b 1
)

if not exist "barangay_server.exe" (
  echo [ERROR] barangay_server.exe not found in this folder.
  exit /b 1
)

powershell -ExecutionPolicy Bypass -File "windows-scripts\install-service.ps1" -AppDir "%cd%" -ExecutablePath "%cd%\barangay_server.exe"

echo.
echo Service install command finished.
endlocal
