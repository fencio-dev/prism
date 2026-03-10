# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Prism CLI binary.

a = Analysis(
    ['prism_cli/prism.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'typer',
        'typer.main',
        'typer.core',
        'click',
        'rich',
        'rich.console',
        'rich.table',
        'rich.markup',
        'rich.text',
        'httpx',
        'httpx._transports.default',
        'dotenv',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='prism',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    onefile=True,
)
