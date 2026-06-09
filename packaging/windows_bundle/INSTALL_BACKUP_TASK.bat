@echo off
setlocal

cd /d %~dp0

rem Navigate to project root (two levels up from packaging\windows_bundle\)
cd ..\..

if not exist "scripts\windows\install-backup-task.ps1" (
  echo [ERROR] scripts\windows\install-backup-task.ps1 not found.
  echo Make sure you are running this from packaging\windows_bundle\
  exit /b 1
)

powershell -ExecutionPolicy Bypass -File "scripts\windows\install-backup-task.ps1" -SourceDb "%cd%\data\barangay.db" -BackupDir "%cd%\data\backups" -RunAt "18:00"

echo.
echo Backup task install command finished.
endlocal
