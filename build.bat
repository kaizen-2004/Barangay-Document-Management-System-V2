@echo off
title Build Barangay System .exe
cd /d "%~dp0"

where uv >nul 2>&1
if %ERRORLEVEL% neq 0 (
  echo [ERROR] uv is required. Install it with: pip install uv
  pause
  exit /b 1
)

echo Building executable (this may take a few minutes)...
uv run pyinstaller packaging\barangay_server.spec --clean

echo.
echo ============================================
echo  Build complete!
echo.
echo  The executable is at:
echo    dist\barangay_server.exe
echo.
echo  Copy it to the target Windows computer.
echo  No Python or Git is required for the executable.
echo ============================================
pause
