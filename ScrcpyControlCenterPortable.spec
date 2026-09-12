# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['scrcpy_gui.py'],
    pathex=[],
    binaries=[('.venv/Lib/site-packages/PySide6/concrt140.dll', 'PySide6'), ('.venv/Lib/site-packages/PySide6/msvcp140_codecvt_ids.dll', 'PySide6')],
    datas=[('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyinstaller_runtime_hook.py'],
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
    icon='assets/scrcpy-control-center.ico',
    name='ScrcpyControlCenterPortable',
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
