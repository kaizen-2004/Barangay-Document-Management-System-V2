@echo off
title Barangay System
cd /d "%~dp0"

:: Choose EXE or source
if exist "dist\BarangaySystem\BarangaySystem.exe" (
    set EXE=dist\BarangaySystem\BarangaySystem.exe
) else (
    call venv\Scripts\activate
    set EXE=python run_server.py
)

:: --ssl flag? Pass it through
if "%1"=="--ssl" (
    start "Barangay Server" /MIN %EXE% --ssl --gen-cert
) else (
    start "Barangay Server" /MIN %EXE%
)

timeout /t 3 /nobreak >nul

echo ============================================
if "%1"=="--ssl" (
    echo  Barangay System (HTTPS mode)
    echo  Browser should open automatically.
    echo  If not: https://localhost:5000
    echo  Accept the self-signed cert warning.
) else (
    echo  Barangay System is running
    echo  Browser should open automatically.
    echo  If not: http://localhost:5000
    echo  Camera works on this computer.
)
echo ============================================
echo  Close "Barangay Server" window to stop.
echo.
echo  If other PCs can't access this system,
echo  run setup-firewall.bat as Administrator.
echo ============================================
pause