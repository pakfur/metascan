# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

# Get the current working directory
project_root = os.path.dirname(os.path.abspath(SPEC))

# Analysis configuration
a = Analysis(
    ['main.py'],
    pathex=[project_root],
    binaries=[],
    datas=[
        # Bundle distribution config.json
        ('config_dist.json', '.'),
        # Bundle icon.png 
        ('icon.png', '.'),
        # Include any other necessary data files
    ],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui', 
        'PyQt6.QtWidgets',
        'PIL',
        'PIL.Image',
        'ffmpeg',
        'nltk',
        'nltk.tokenize',
        'watchdog',
        'watchdog.observers',
        'watchdog.events',
        'dataclasses_json',
        'marshmallow',
        'sqlite3',
    ],
    hookspath=['.'],  # Include current directory for custom hooks
    hooksconfig={},
    runtime_hooks=['runtime_hook.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# PYZ archive
pyz = PYZ(a.pure)

# Executable configuration
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MetaScan',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Set to False for GUI application
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='MetaScan.icns',  # Set the application icon
)

# App bundle for macOS
app = BUNDLE(
    exe,
    name='MetaScan.app',
    icon='MetaScan.icns',
    bundle_identifier='com.metascan.app',
    version='1.0.0',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSAppleScriptEnabled': False,
        'CFBundleName': 'MetaScan',
        'CFBundleDisplayName': 'MetaScan',
        'CFBundleGetInfoString': 'MetaScan Media Scanner',
        'CFBundleIdentifier': 'com.metascan.app',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHumanReadableCopyright': 'Copyright © 2024 MetaScan. All rights reserved.',
        'NSHighResolutionCapable': True,
    },
)