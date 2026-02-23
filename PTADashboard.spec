# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import numpy as np
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

streamlit_datas, streamlit_binaries, streamlit_hiddenimports = collect_all("streamlit")
streamlit_metadata = copy_metadata("streamlit")

numpy_datas, numpy_binaries, numpy_hiddenimports = collect_all("numpy")
pandas_datas, pandas_binaries, pandas_hiddenimports = collect_all("pandas")

datas = []
binaries = []
hiddenimports = []

datas += streamlit_datas
datas += streamlit_metadata
binaries += streamlit_binaries
hiddenimports += streamlit_hiddenimports

datas += numpy_datas + pandas_datas
binaries += numpy_binaries + pandas_binaries
hiddenimports += numpy_hiddenimports + pandas_hiddenimports

numpy_dir = Path(np.__file__).resolve().parent
for pyd in (numpy_dir / "core").glob("*.pyd"):
    binaries.append((str(pyd), "numpy/core"))
for pyd in (numpy_dir / "_core").glob("*.pyd"):
    binaries.append((str(pyd), "numpy/_core"))

hiddenimports += collect_submodules("duckdb")
hiddenimports += [
    "app",
    "daily_report",
    "persistence",
    "trade_matching",
    "normalization",
    "importers.mt5_xlsx",
    "importers.ninjatrader_csv",
]

# Ensure dashboard wrapper script is available inside the bundled app.
datas += [("src\\pta_dashboard\\dashboard_entry.py", "pta_dashboard")]

block_cipher = None

a = Analysis(
    ["src\\pta_dashboard_main.py"],
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
    name="PTADashboard",
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
    name="PTADashboard",
)
