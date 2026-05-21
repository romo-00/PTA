# Release Build

## Prerequisites

- Python environment with project dependencies installed.
- Inno Setup installed on build machine.
- `ISCC.exe` available (default expected path):
  - `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`

Install Python deps:

```powershell
py -m pip install -r requirements-desktop.txt
```

## One-command build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
```

This command will:
- build `dist\PTAAgent\PTAAgent.exe` (ONEDIR)
- build `dist\PTADashboard\PTADashboard.exe` (ONEDIR)
- compile installer to:
  - `Output\PTA_Setup.exe`
