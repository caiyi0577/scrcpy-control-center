# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


app_data = Path(os.environ.get("LOCALAPPDATA", ""))
configured_dir = os.environ.get("SCRCPY_DIR", "")
official_candidates = []
if configured_dir:
    official_candidates.append(Path(configured_dir))
official_candidates.append(
    app_data / "Microsoft" / "WinGet" / "Packages" / "Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe" / "scrcpy-win64-v4.1"
)
official_dir = next(
    (path for path in official_candidates if (path / "scrcpy.exe").is_file()),
    None,
)
if official_dir is None:
    raise SystemExit("Could not find the official scrcpy v4.1 directory to embed.")


a = Analysis(
    ['scrcpy_gui.py'],
    pathex=['.venv/Lib/site-packages/win32', '.venv/Lib/site-packages/win32/lib'],
    binaries=[
        ('.venv/Lib/site-packages/PySide6/concrt140.dll', 'PySide6'),
        ('.venv/Lib/site-packages/PySide6/msvcp140_codecvt_ids.dll', 'PySide6'),
    ],
    datas=[
        ('assets', 'assets'),
        (str(official_dir), 'scrcpy'),
    ],
    hiddenimports=['win32gui', 'win32process'],
    hookspath=[],
    hooksconfig={},
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Qt6Core.dll imports the unversioned ICU API. PyInstaller can accidentally
# collect the Codex runtime's ICU 78 DLL, which breaks Qt startup with a
# missing ucnv_open entry point.
a.binaries = [
    item for item in a.binaries
    if item[0].lower() not in {"icuuc.dll", "icudt78.dll"}
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ScrcpyControlCenterSingle',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
