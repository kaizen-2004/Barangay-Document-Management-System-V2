@echo off
title Barangay System
cd /d "%~dp0"

:: Prefer the optional executable; otherwise run the source checkout with uv.
if exist "barangay_server.exe" (
    set "EXE=barangay_server.exe"
) else if exist "dist\barangay_server.exe" (
    set "EXE=dist\barangay_server.exe"
) else (
    where uv >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] uv is required for source deployment.
        echo Install it with: pip install uv
        pause
        exit /b 1
    )
    set "EXE=uv run python run_server.py"
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
