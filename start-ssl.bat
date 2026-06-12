@echo off
title Barangay System
cd /d "%~dp0"

:: Use the PyInstaller .exe if available (built via build.bat)
if exist "dist\BarangaySystem\BarangaySystem.exe" (
    start "Barangay Server" /MIN "dist\BarangaySystem\BarangaySystem.exe"
) else (
    call venv\Scripts\activate
    start "Barangay Server" /MIN python run_server.py
)

timeout /t 5 /nobreak >nul

echo ============================================
echo  Barangay System is running
echo  Open: http://localhost:5000
echo  Camera works on this computer.
echo  Close "Barangay Server" window to stop.
echo ============================================
pause
