# PTA Project Context

## 1) Purpose
PTA is a post-trade analytics system for comparing two trade histories:
- Source account
- Target account

Primary output is matched/unmatched analysis and slippage/performance deltas.

## 2) High-Level Architecture
- PTAAgent (Windows executable/CLI):
  - Connects to MetaTrader 5 Python API.
  - Ingests deals into DuckDB.
- PTADashboard (Streamlit app):
  - Loads file-based inputs and/or persisted run data.
  - Runs reconciliation and renders analytics views.
- DuckDB persistence:
  - Stores ingested MT5 deals and run history/metadata.

## 3) Data Flow
MT5 terminal/API -> PTAAgent ingest -> DuckDB (`mt5_deals`, state, run metadata) -> PTADashboard compare/reports/UI.

File-based mode is also supported:
- Source/Target files -> normalization/importers -> matching -> dashboard views/exports.

## 4) Matching Logic
Conceptually organized as Source vs Target reconciliation with strict + fallback modes.

Current implementation in code is sequential pairing:
- Sort Source and Target by entry time.
- Pair by index up to min(count_source, count_target).
- Remaining rows are unmatched (`SEQUENCE_DRIFT`).

Configuration knobs include quantity conversion and Source entry time shift.

## 5) Slippage Definition
Per matched trade, signed points deltas are side-adjusted:
- BUY: delta = target - source
- SELL: delta = source - target

Entry/exit slippage columns are represented in points and aggregated in the dashboard.
For XAUUSD, point scaling is treated as 0.01 price units per point.

## 6) CLI Commands
Agent commands:
- `PTAAgent ingest --db <path> [--lookback-minutes N] [--reset-watermark] [--mt5-terminal-path <terminal64.exe>]`
- `PTAAgent status --db <path> [--mt5-terminal-path <terminal64.exe>]`
- `PTAAgent debug-mt5 [--mt5-terminal-path <terminal64.exe>]`

Dashboard launch:
- `python -m streamlit run src\pta_dashboard\dashboard_entry.py --server.headless true`

## 7) Installer Behavior
Build script (`scripts/build_installer.ps1`) performs deterministic rebuild:
- Cleans previous artifacts (`build`, `dist`, `installer\Output`).
- Rebuilds Agent and Dashboard via PyInstaller.
- Compiles Inno Setup installer into `installer\Output`.
- Produces:
  - Versioned installer: `PTA_setup_<version>.exe`
  - Canonical alias: `PTA_setup.exe`

Installer also writes installed version metadata to:
- `%PROGRAMDATA%\PTA\version.txt`

## 8) Current Known Constraints
- MT5 API history visibility can differ from what is shown in MT5 UI.
- `history_deals_get(from, to)` may under-return on some environments; ingest relies on wide fetch + Python-side filtering/watermarking.
- Correct terminal binding is critical; `--mt5-terminal-path` is used to force instance selection.

---
Manual-only policy:
- Do not auto-update this file.
- Update only when explicitly instructed by the user.
