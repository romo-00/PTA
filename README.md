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

Use `requirements.txt` for the Streamlit dashboard runtime, including Streamlit
Community Cloud. For the Windows desktop agent/installer build, install:

```powershell
py -m pip install -r requirements-desktop.txt
```

For local test runs, install:

```powershell
py -m pip install -r requirements-dev.txt
```

## Deploy to Streamlit Community Cloud

1. Push this repository to GitHub.
2. In Streamlit Community Cloud, create a new app from the GitHub repository.
3. Set the main file path to `app.py`.
4. Click **Advanced settings** and choose Python 3.12. Community Cloud does not
   use `runtime.txt` to change Python for an already-created app; delete and
   redeploy the app if it was created with the wrong Python version.
5. Deploy. Streamlit will install `requirements.txt` and system packages from
   `packages.txt`.
6. Use **Upload files** in the sidebar. Local path loading only sees files that
   exist inside the deployed container, not files on your computer.

Community Cloud storage is ephemeral. Uploaded files, `reports/`, and the
default DuckDB history at `data/pta.duckdb` can disappear on app restart,
redeploy, or sleep/wake cycles. Download reports you want to keep, or set
`PTA_DB_PATH` / `PTA_REPORTS_DIR` for another hosting target with persistent
storage.

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
