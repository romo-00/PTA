from __future__ import annotations

from fnmatch import fnmatch
from typing import Iterable

import pandas as pd

NORMALIZED_COLUMNS = [
    "source",
    "trade_id",
    "symbol_raw",
    "symbol_norm",
    "side",
    "qty",
    "entry_time_utc",
    "exit_time_utc",
    "entry_price",
    "exit_price",
    "net_profit",
]


def normalize_symbol(symbol: str, symbol_map: dict[str, str]) -> str:
    value = (symbol or "").strip().upper()
    for pattern, target in symbol_map.items():
        if fnmatch(value, pattern.upper()):
            return target
    return value


def normalize_side(value: str) -> str:
    raw = (value or "").strip().upper()
    if raw in {"BUY", "LONG"}:
        return "BUY"
    if raw in {"SELL", "SHORT"}:
        return "SELL"
    return raw


def ensure_columns(df: pd.DataFrame, required: Iterable[str], source_name: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required {source_name} columns: {missing}")


def to_utc_naive(ts: pd.Series, timezone_name: str) -> pd.Series:
    localized = pd.to_datetime(ts, errors="coerce")
    localized = localized.dt.tz_localize(timezone_name, ambiguous="NaT", nonexistent="shift_forward")
    return localized.dt.tz_convert("UTC").dt.tz_localize(None)


def coerce_normalized(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["source"] = out["source"].astype(str)
    out["trade_id"] = out["trade_id"].astype(str)
    out["symbol_raw"] = out["symbol_raw"].astype(str)
    out["symbol_norm"] = out["symbol_norm"].astype(str)
    out["side"] = out["side"].map(normalize_side)
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce")
    out["entry_time_utc"] = pd.to_datetime(out["entry_time_utc"], errors="coerce")
    out["exit_time_utc"] = pd.to_datetime(out["exit_time_utc"], errors="coerce")
    out["entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
    out["exit_price"] = pd.to_numeric(out["exit_price"], errors="coerce")
    out["net_profit"] = pd.to_numeric(out["net_profit"], errors="coerce")
    return out[NORMALIZED_COLUMNS]
