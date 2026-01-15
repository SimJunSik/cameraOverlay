# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [('/Users/junsik/Desktop/cameraOverlay/.venv/lib/python3.11/site-packages/mediapipe/modules', 'mediapipe/modules')]
hiddenimports = []
datas += collect_data_files('mediapipe')
hiddenimports += collect_submodules('mediapipe')
hiddenimports += collect_submodules('AVFoundation')
hiddenimports += collect_submodules('objc')


a = Analysis(
    ['app.py'],
    pathex=[],
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
    [],
    exclude_binaries=True,
    name='CameraOverlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CameraOverlay',
)
app = BUNDLE(
    coll,
    name='CameraOverlay.app',
    icon='assets/icon.icns',
    bundle_identifier='com.example.cameraoverlay',
)
