# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path.cwd()
PKG_DIR = ROOT / "barangay_project"


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
datas.extend(_collect_files(PKG_DIR / "templates", "barangay_project/templates"))
datas.extend(_collect_files(PKG_DIR / "static", "barangay_project/static"))


a = Analysis(
    ["run_server.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
