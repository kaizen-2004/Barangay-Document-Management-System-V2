@echo off
title Build Barangay System .exe
cd /d "%~dp0"

echo Installing/upgrading PyInstaller...
python -m pip install --upgrade pyinstaller -q

echo Building executable (this may take a few minutes)...
pyinstaller --onedir --noconsole --name "BarangaySystem" ^
  --add-data "frontend\templates;frontend\templates" ^
  --add-data "frontend\static;frontend\static" ^
  --add-data "migrations;migrations" ^
  --add-data "frontend\static\uploads\doc_templates;frontend\static\uploads\doc_templates" ^
  --hidden-import flask ^
  --hidden-import flask_sqlalchemy ^
  --hidden-import flask_login ^
  --hidden-import flask_migrate ^
  --hidden-import flask_wtf ^
  --hidden-import sqlalchemy ^
  --hidden-import alembic ^
  --hidden-import docxtpl ^
  --hidden-import PIL ^
  --hidden-import PIL._tkinter_finder ^
  --hidden-import cv2 ^
  --hidden-import rembg ^
  --hidden-import onnxruntime ^
  --hidden-import qrcode ^
  --hidden-import reportlab ^
  --hidden-import openpyxl ^
  --hidden-import cryptography ^
  --hidden-import waitress ^
  --hidden-import email_validator ^
  --hidden-import dotenv ^
  --hidden-import numpy ^
  run_server.py

echo.
echo ============================================
echo  Build complete!
echo.
echo  The executable is at:
echo    dist\BarangaySystem\BarangaySystem.exe
echo.
echo  Create a desktop shortcut pointing to it.
echo  Double-click to start the system.
echo ============================================
pause
