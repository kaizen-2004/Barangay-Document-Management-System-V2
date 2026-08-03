# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


SPEC_DIR = Path(globals().get("SPECPATH", Path.cwd()))
ROOT = SPEC_DIR.parent


def _collect_files(base: Path, target_prefix: str):
    pairs = []
    if not base.exists():
        return pairs
    for path in base.rglob("*"):
        if path.is_file():
            rel_parent = path.parent.relative_to(base)
            target_dir = str(Path(target_prefix) / rel_parent)
            pairs.append((str(path), target_dir))
    return pairs


datas = []
datas.extend(_collect_files(ROOT / "frontend" / "templates", "frontend/templates"))
datas.extend(_collect_files(ROOT / "frontend" / "static", "frontend/static"))
datas.extend(_collect_files(ROOT / "migrations", "migrations"))

hiddenimports = [
    "flask",
    "flask_sqlalchemy",
    "flask_login",
    "flask_migrate",
    "flask_wtf",
    "sqlalchemy",
    "alembic",
    "docxtpl",
    "PIL",
    "PIL._tkinter_finder",
    "cv2",
    "rembg",
    "onnxruntime",
    "qrcode",
    "reportlab",
    "openpyxl",
    "cryptography",
    "waitress",
    "email_validator",
    "dotenv",
    "numpy",
    "werkzeug",
]


a = Analysis(
    [str(ROOT / "run_server.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="barangay_server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
