#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "Building BarangaySystem.exe via Docker cross-compile..."

docker run --rm -v "$(pwd):/src" cdrx/pyinstaller-windows:latest \
  -y --onedir --noconsole --name "BarangaySystem" \
  --add-data "frontend/templates;frontend/templates" \
  --add-data "frontend/static;frontend/static" \
  --add-data "migrations;migrations" \
  --hidden-import flask \
  --hidden-import flask_sqlalchemy \
  --hidden-import flask_login \
  --hidden-import flask_migrate \
  --hidden-import flask_wtf \
  --hidden-import sqlalchemy \
  --hidden-import alembic \
  --hidden-import docxtpl \
  --hidden-import PIL \
  --hidden-import PIL._tkinter_finder \
  --hidden-import cv2 \
  --hidden-import rembg \
  --hidden-import onnxruntime \
  --hidden-import qrcode \
  --hidden-import reportlab \
  --hidden-import openpyxl \
  --hidden-import cryptography \
  --hidden-import waitress \
  --hidden-import email_validator \
  --hidden-import dotenv \
  --hidden-import numpy \
  run_server.py

echo ""
echo "============================================"
echo " Build complete!"
echo ""
echo " The .exe is at: dist/BarangaySystem/BarangaySystem.exe"
echo " Copy the whole 'dist/BarangaySystem' folder to any Windows PC"
echo " and double-click BarangaySystem.exe to run."
echo "============================================"
