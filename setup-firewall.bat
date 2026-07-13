@echo off
title Barangay System - Firewall Setup
echo ============================================
echo  Barangay System - Windows Firewall Setup
echo ============================================
echo.
echo This will add a firewall rule to allow
echo access to the Barangay System on port 5000.
echo.
echo You may be prompted for administrator permission.
echo.

:: Check for admin privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo Adding firewall rule for Barangay System (port 5000)...
netsh advfirewall firewall add rule name="Barangay System (HTTP)" dir=in action=allow protocol=TCP localport=5000

if %errorLevel% equ 0 (
    echo.
    echo ============================================
    echo  SUCCESS: Firewall rule added!
    echo.
    echo  The Barangay System is now accessible via:
    echo    http://localhost:5000
    echo    http://YOUR_IP_ADDRESS:5000
    echo.
    echo  Other devices on your network can now
    echo    http://YOUR_IP_ADDRESS:5000
    echo.
    echo  To find your IP address, run: ipconfig
    echo ============================================
) else (
    echo.
    echo ============================================
    echo  ERROR: Failed to add firewall rule.
    echo  Try running this script as Administrator.
    echo ============================================
)
pause
