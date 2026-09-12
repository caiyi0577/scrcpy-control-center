# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['scrcpy_gui.py'],
    pathex=['.venv/Lib/site-packages/win32', '.venv/Lib/site-packages/win32/lib'],
    binaries=[('.venv/Lib/site-packages/PySide6/concrt140.dll', 'PySide6'), ('.venv/Lib/site-packages/PySide6/msvcp140_codecvt_ids.dll', 'PySide6')],
    datas=[('assets', 'assets')],
    hiddenimports=['win32gui', 'win32process'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyinstaller_runtime_hook.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt6Core.dll imports the unversioned ICU API. PyInstaller can accidentally
# collect the Codex runtime's ICU 78 DLL, which only exports *_78 symbols and
# then breaks Qt startup with a missing ucnv_open entry point. Let Windows use
# its compatible ICU forwarder instead.
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
    icon='assets/scrcpy-control-center.ico',
    name='ScrcpyControlCenterFinal',
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
