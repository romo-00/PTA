from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is importable when running this wrapper via Streamlit.
ROOT = Path(__file__).resolve().parents[2]
THIS_DIR = Path(__file__).resolve().parent

# Avoid importing a stale local "app.py" from this wrapper folder.
while str(THIS_DIR) in sys.path:
    sys.path.remove(str(THIS_DIR))

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import main  # noqa: E402


if __name__ == "__main__":
    main()
