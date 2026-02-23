# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import numpy as np
from PyInstaller.utils.hooks import collect_all, collect_submodules

numpy_datas, numpy_binaries, numpy_hidden = collect_all("numpy")
pandas_datas, pandas_binaries, pandas_hidden = collect_all("pandas")
mt5_datas, mt5_binaries, mt5_hidden = collect_all("MetaTrader5")

hiddenimports = []
hiddenimports += collect_submodules("duckdb")
hiddenimports += numpy_hidden
hiddenimports += pandas_hidden
hiddenimports += mt5_hidden

datas = []
datas += numpy_datas
datas += pandas_datas
datas += mt5_datas

binaries = []
binaries += numpy_binaries
binaries += pandas_binaries
binaries += mt5_binaries

numpy_dir = Path(np.__file__).resolve().parent
for pyd in (numpy_dir / "core").glob("*.pyd"):
    binaries.append((str(pyd), "numpy/core"))
for pyd in (numpy_dir / "_core").glob("*.pyd"):
    binaries.append((str(pyd), "numpy/_core"))

block_cipher = None

a = Analysis(
    ['src\\pta_agent_main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PTAAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='PTAAgent',
)
