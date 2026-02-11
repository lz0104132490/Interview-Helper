# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules


block_cipher = None


script_dir = os.path.dirname(__file__)
main_script = os.path.join(script_dir, "main.py")

datas = []
binaries = []

hiddenimports = []
hiddenimports += collect_submodules("sounddevice")
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += collect_submodules("torch")
hiddenimports += collect_submodules("pyannote")


a = Analysis(
    [main_script],
    pathex=[script_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="desktop-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
