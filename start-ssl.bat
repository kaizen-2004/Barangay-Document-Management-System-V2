@echo off
title Barangay System
cd /d "%~dp0"

:: Use the PyInstaller .exe if available (built via build.bat)
if exist "dist\BarangaySystem\BarangaySystem.exe" (
    start "Barangay Server" /MIN "dist\BarangaySystem\BarangaySystem.exe"
) else (
    call venv\Scripts\activate
    start "Barangay Server" /MIN python run_server.py --ssl --gen-cert
)

timeout /t 5 /nobreak >nul

python -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print('Your IP is:', s.getsockname()[0]); s.close()"

echo ============================================
echo  Barangay System is running
echo  On THIS PC: https://localhost:5000
echo  On MOBILE:  https://<IP above>:5000
echo  (Accept the self-signed cert warning)
echo  Close "Barangay Server" window to stop.
echo ============================================
start https://localhost:5000
pause
