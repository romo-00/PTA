# Backtest vs Live Trade Comparison Dashboard

## Daily Workflow (Recommended)

1. Export reports:
- MT5 Strategy Tester report (HTML) to `data/raw/mt5_backtest/latest.html`
- MT5 Live Trade History report (HTML) to `data/raw/mt5_live/latest.html`

2. Open dashboard:

```powershell
py -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

3. In sidebar:
- Set `Input format` to `MT5 Backtest HTML vs MT5 Live HTML`
- Upload both HTML files (or use local paths)
- Click **Run comparison**

## Install

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

## Supported Inputs

- MT5 Strategy Tester report: `.html` / `.htm`
- MT5 Live Trade History report: `.html` / `.htm`
- Legacy mode still available: NinjaTrader CSV vs MT5 XLSX

## What the dashboard focuses on

- Model-to-Live Entry Difference (pts)
- Model-to-Live Exit Difference (pts)
- Total points on matched completed trades (backtest vs live)

## Persistence (DuckDB)

Database file: `data/pta.duckdb` (or `C:\ProgramData\PTA\pta.duckdb` in packaged app)

## Tests

```powershell
py -m pytest
```
