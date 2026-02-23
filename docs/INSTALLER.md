# PTA Installer (Agent + Dashboard)

## Prerequisites

- MetaTrader 5 terminal is installed on the VPS.
- MT5 terminal is running and logged in to the target account.
- No MT5 credentials are collected by PTA.

## What the installer does

- Installs Agent to: `{app}\Agent`
- Installs Dashboard to: `{app}\Dashboard`
- Creates:
  - `C:\ProgramData\PTA`
  - `C:\ProgramData\PTA\logs`
- Runs one ingest attempt:
  - `PTAAgent.exe ingest --db "C:\ProgramData\PTA\pta.duckdb"`
- Registers scheduled task:
  - `PTA MT5 Ingest` every 5 minutes
- Adds shortcuts:
  - Start Menu: `PTA Dashboard`
  - Desktop: `PTA Dashboard`

## Verify ingestion is running

1. Check scheduled task:

```powershell
schtasks /Query /TN "PTA MT5 Ingest" /V /FO LIST
```

2. Check log output:

- `C:\ProgramData\PTA\logs\mt5_ingest.log`

Expected:
- periodic runs every 5 minutes
- ingest window/watermark log lines
- if MT5 is unavailable, clear message with `last_error`

## Verify dashboard

- Launch `PTA Dashboard` from Start Menu or Desktop shortcut.
- It starts Streamlit on `http://localhost:8501` and opens the browser.
- Dashboard defaults to reading `C:\ProgramData\PTA\pta.duckdb` in read-only mode.
