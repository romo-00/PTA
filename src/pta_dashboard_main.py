from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from streamlit.web import cli as st_cli

COMMON_PTA_DIR = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "PTA"

HOST = "127.0.0.1"
PORT = 8501
APP_URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{APP_URL}/_stcore/health"
LOG_PATH = COMMON_PTA_DIR / "logs" / "dashboard.log"


def _resolve_app_path() -> Path:
    if getattr(sys, "frozen", False):
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "pta_dashboard" / "dashboard_entry.py")
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "_internal" / "pta_dashboard" / "dashboard_entry.py")
        candidates.append(exe_dir / "pta_dashboard" / "dashboard_entry.py")

        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError(
            f"Unable to locate bundled dashboard dashboard_entry.py. Checked: {[str(p) for p in candidates]}"
        )

    return Path(__file__).resolve().parent / "pta_dashboard" / "dashboard_entry.py"


def _wait_for_streamlit_and_open_browser() -> None:
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1.0) as response:
                if response.status == 200:
                    webbrowser.open(APP_URL)
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.25)


def main() -> int:
    os.environ.setdefault("PTA_DB_PATH", str(COMMON_PTA_DIR / "pta.duckdb"))
    os.environ.setdefault("PTA_DB_READ_ONLY", "1")
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8", buffering=1) as log_file:
        with contextlib.redirect_stdout(log_file), contextlib.redirect_stderr(log_file):
            script_path = _resolve_app_path()
            print(f"[dashboard] starting streamlit script={script_path} host={HOST} port={PORT}")

            opener = threading.Thread(target=_wait_for_streamlit_and_open_browser, daemon=True)
            opener.start()

            argv = [
                "streamlit",
                "run",
                str(script_path),
                "--server.address",
                HOST,
                "--server.port",
                str(PORT),
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
                "--global.developmentMode",
                "false",
            ]
            original_argv = sys.argv
            try:
                sys.argv = argv
                st_cli.main()
            finally:
                sys.argv = original_argv

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
