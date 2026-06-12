@echo off
title Barangay System - Cloudflare Tunnel
cd /d "%~dp0"

echo ============================================
echo  Starting Cloudflare Tunnel...
echo  This creates a public URL for QR codes.
echo ============================================
echo.

where cloudflared >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo cloudflared not found. Installing via winget...
    winget install cloudflare.cloudflared
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo Install cloudflared manually from:
        echo   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
        echo.
        pause
        exit /b 1
    )
)

echo Starting tunnel on port 5000...
echo Once started, copy the URL and set PUBLIC_URL in .env
echo.
cloudflared tunnel --url http://localhost:5000
pause
