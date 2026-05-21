from __future__ import annotations

from pathlib import Path
from io import BytesIO
from uuid import uuid4
from zoneinfo import available_timezones

import numpy as np
import os
import pandas as pd
import streamlit as st

from daily_report import generate_daily_report_xlsx
from importers.mt5_html import load_mt5_html
from importers.mt5_xlsx import load_mt5_xlsx, peek_mt5_xlsx_columns
from importers.ninjatrader_csv import NT_TIMEZONE, load_ninjatrader_csv, load_ninjatrader_xlsx
from persistence import load_run, persist_run, run_history, trend_history
from trade_matching import MatchConfig, match_trades

DEFAULT_SYMBOL_MAP = {
    "MNQ*": "NAS100",
    "USTEC*": "NAS100",
    "NAS100*": "NAS100",
    "MGC*": "XAUUSD",
    "XAU32*": "XAUUSD",
    "XAUUSD*": "XAUUSD",
    "BTC*": "BTCUSD",
    "BTCUSD*": "BTCUSD",
}
DEFAULT_AUD_RATE_MAP = {
    "2024-01": 1.505310,
    "2024-02": 1.532870,
    "2024-03": 1.525192,
    "2024-04": 1.537336,
    "2024-05": 1.508143,
    "2024-06": 1.505013,
    "2024-07": 1.497766,
    "2024-08": 1.502867,
    "2024-09": 1.476721,
    "2024-10": 1.491745,
    "2024-11": 1.532142,
    "2024-12": 1.580303,
    "2025-01": 1.606062,
    "2025-02": 1.586317,
    "2025-03": 1.588959,
    "2025-04": 1.590766,
    "2025-05": 1.553224,
    "2025-06": 1.537669,
    "2025-07": 1.527576,
    "2025-08": 1.539030,
    "2025-09": 1.517778,
    "2025-10": 1.529085,
    "2025-11": 1.537319,
    "2025-12": 1.504239,
    "2026-01": 1.475512,
    "2026-02": 1.417203,
    "2026-03": 1.419078,
}
REPORTS_DIR = Path(os.getenv("PTA_REPORTS_DIR", "reports"))
SUPPORTED_UPLOAD_TYPES = ["csv", "xlsx", "html", "htm"]
DEFAULT_LEFT_PATH = "data/raw/source/latest.html"
DEFAULT_RIGHT_PATH = "data/raw/target/latest.html"
MT5_BROKER_DST_TZ = "Europe/Helsinki"
MT5_TIMEZONE_LABELS = {
    MT5_BROKER_DST_TZ: "Broker DST (UTC+2 winter / UTC+3 summer)",
    "UTC": "UTC",
    "Etc/GMT-2": "Fixed UTC+2",
    "Etc/GMT-3": "Fixed UTC+3",
}

STRATEGY_HINTS = {
    "NADQ33": ("nq33", "nadq33", "33min"),
    "MBF": (" mb ", "mbf", "market break", "nq mb"),
    "XAU32": ("xau32", "xau 32", "gold32"),
}


def _read_installed_version() -> str | None:
    version_path = Path(__file__).resolve().parent / "VERSION.txt"
    try:
        if version_path.exists():
            text = version_path.read_text(encoding="utf-8").strip()
            return text or None
    except Exception:
        return None
    return None


def _read_packaged_version() -> str | None:
    version_path = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "PTA" / "version.txt"
    try:
        if version_path.exists():
            text = version_path.read_text(encoding="utf-8").strip()
            return text or None
    except Exception:
        return None
    return None


def _guess_mt5_role(filename: str) -> str | None:
    name = filename.lower()
    backtest_tokens = ("backtest", "strategy", "tester", "optimization", "report")
    live_tokens = ("live", "history", "trade history", "account history", "real")
    if any(token in name for token in backtest_tokens):
        return "backtest"
    if any(token in name for token in live_tokens):
        return "live"
    return None


def _infer_strategy_tag_from_filename(filename: str) -> str | None:
    name = f" {Path(filename or '').stem.lower()} "
    for tag, hints in STRATEGY_HINTS.items():
        if any(hint in name for hint in hints):
            return tag
    return None


def _strategy_tag_options(*dfs: pd.DataFrame) -> list[str]:
    tags: set[str] = set()
    for df in dfs:
        if df is None or df.empty or "strategy_tag" not in df.columns:
            continue
        values = df["strategy_tag"].astype(str).str.strip()
        tags.update(tag.upper() for tag in values if tag and tag.lower() not in {"nan", "none"})
    return sorted(tags)


def _default_strategy_tag_filter(left_kind: str | None, right_kind: str | None, options: list[str], left_filename: str, right_filename: str) -> str:
    left_hint = _infer_strategy_tag_from_filename(left_filename)
    right_hint = _infer_strategy_tag_from_filename(right_filename)
    for hint in [left_hint, right_hint]:
        if hint and hint in options:
            return hint
    mt5_tester_vs_live = {left_kind, right_kind} == {"mt5_backtest_html", "mt5_live_html"}
    if mt5_tester_vs_live and "MBF" in options:
        return "MBF"
    return "All"


def _peek_table_columns(input_obj, filename: str) -> list[str]:
    suffix = Path(filename).suffix.lower()
    if hasattr(input_obj, "seek"):
        try:
            input_obj.seek(0)
        except Exception:
            pass
    if suffix == ".csv":
        cols = list(pd.read_csv(input_obj, nrows=0).columns)
    elif suffix == ".xlsx":
        cols = peek_mt5_xlsx_columns(input_obj)
    else:
        cols = []
    if hasattr(input_obj, "seek"):
        try:
            input_obj.seek(0)
        except Exception:
            pass
    return [str(c) for c in cols]


def _is_ctrader_xlsx_columns(cols: set[str]) -> bool:
    return {"Symbol", "Opening direction", "Opening time", "Closing time", "Entry price", "Closing price", "Closing Quantity", "Net $"}.issubset(cols)


def _infer_kind(input_obj, filename: str, role: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return "nt_csv"
    if suffix in {".html", ".htm"}:
        return "mt5_backtest_html" if role == "source" else "mt5_live_html"
    if suffix == ".xlsx":
        cols = set(_peek_table_columns(input_obj, filename))
        if {"Market pos.", "Qty", "Entry price", "Exit price", "Entry time", "Exit time", "Profit"}.issubset(cols):
            return "nt_xlsx"
        if {"Type", "Ticket", "Symbol", "Lots", "Buy/sell", "Open price", "Close price"}.issubset(cols):
            return "mt5_xlsx"
        if {"Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price"}.issubset(cols):
            return "mt5_xlsx"
        if {"Time", "Deal", "Type", "Direction", "Volume", "Price", "Profit", "Balance"}.issubset(cols):
            return "mt5_xlsx"
        if {"Symbol", "Opening direction", "Opening time", "Closing time", "Entry price", "Closing price", "Closing Quantity", "Net $"}.issubset(cols):
            return "mt5_xlsx"
        if {"Time", "Position", "Symbol", "Type", "Volume", "Price", "Time.1", "Price.1", "Profit"}.issubset(cols):
            return "mt5_xlsx"
        raise ValueError(f"Unsupported XLSX schema for {filename!r}. Found columns: {sorted(cols)}")
    raise ValueError(f"Unsupported file type for {filename!r}. Supported extensions: csv, xlsx, html, htm")


def _kind_label(kind: str) -> str:
    labels = {
        "nt_csv": "CSV",
        "nt_xlsx": "XLSX",
        "mt5_xlsx": "XLSX",
        "mt5_backtest_html": "HTML",
        "mt5_live_html": "HTML",
    }
    return labels.get(kind, kind)


def _median_entry_price(*dfs: pd.DataFrame) -> float | None:
    values: list[pd.Series] = []
    for df in dfs:
        if df is None or df.empty or "entry_price" not in df.columns:
            continue
        values.append(pd.to_numeric(df["entry_price"], errors="coerce").dropna())
    if not values:
        return None
    combined = pd.concat(values, ignore_index=True)
    if combined.empty:
        return None
    return float(combined.median())


def _price_tolerance_from_scale(price_scale: float | None, ratio: float) -> float:
    if price_scale is None or pd.isna(price_scale):
        return 0.0
    return round(max(0.0, float(price_scale) * ratio), 5)


def _suggest_matching_preset(
    left_kind: str | None,
    right_kind: str | None,
    left_filename: str,
    right_filename: str,
    left_columns: set[str],
    right_columns: set[str],
    left_preview: pd.DataFrame,
    right_preview: pd.DataFrame,
) -> dict[str, object]:
    names = f"{left_filename} {right_filename}".lower()
    live_tokens = ("live", "actual", "real", "account", "history")
    backtest_tokens = ("backtest", "tester", "simulation", "sim", "strategy")
    price_scale = _median_entry_price(left_preview, right_preview)

    execution_csv_cols = {"Fin Instrument", "Symbol", "Action", "Quantity", "Price", "Time", "Date"}
    mt5_positions_report_cols = {"Time", "Position", "Symbol", "Type", "Volume", "Price", "Time.1", "Price.1", "Profit"}

    if (execution_csv_cols.issubset(left_columns) and mt5_positions_report_cols.issubset(right_columns)) or (
        execution_csv_cols.issubset(right_columns) and mt5_positions_report_cols.issubset(left_columns)
    ):
        return {
            "label": "Bridge copy comparison",
            "reason": "Detected IBKR-style execution log vs MT5 positions report; use strict timing first.",
            "matching_mode": "time",
            "strict_entry_tolerance_seconds": 5,
            "strict_exit_tolerance_seconds": 5,
            "entry_price_tolerance": 0.0,
            "exit_price_tolerance": 0.0,
        }

    if right_kind == "mt5_xlsx" and {"Time", "Deal", "Type", "Direction", "Volume", "Price", "Profit", "Balance"}.issubset(right_columns) and "Symbol" not in right_columns:
        return {
            "label": "Backtest-style comparison",
            "reason": "Detected reduced MT5 backtest export without a symbol column.",
            "matching_mode": "time",
            "strict_entry_tolerance_seconds": 14400,
            "strict_exit_tolerance_seconds": 14400,
            "entry_price_tolerance": 0.0,
            "exit_price_tolerance": 0.0,
        }

    if left_kind == "mt5_xlsx" and right_kind == "mt5_xlsx" and _is_ctrader_xlsx_columns(left_columns) and {"Time", "Position", "Symbol", "Type", "Volume", "Price", "Time.1", "Price.1", "Profit"}.issubset(right_columns):
        return {
            "label": "cTrader vs MT5 report",
            "reason": "Detected cTrader closed-trades XLSX vs MT5 positions-report XLSX; use strict time matching.",
            "matching_mode": "time",
            "strict_entry_tolerance_seconds": 5,
            "strict_exit_tolerance_seconds": 5,
            "entry_price_tolerance": 0.0,
            "exit_price_tolerance": 0.0,
        }

    if left_kind in {"nt_csv", "nt_xlsx"} and right_kind == "mt5_live_html":
        return {
            "label": "NT backtest vs tagged MT5 actual",
            "reason": "Detected NinjaTrader source vs MT5 account-history HTML; use strategy tag filter when available and match primarily by time.",
            "matching_mode": "time",
            "strict_entry_tolerance_seconds": 21600,
            "strict_exit_tolerance_seconds": 21600,
            "entry_price_tolerance": 0.0,
            "exit_price_tolerance": 0.0,
        }

    if left_kind in {"nt_csv", "nt_xlsx"} and right_kind == "mt5_xlsx":
        ratio = 0.005 if price_scale and price_scale > 1000 else 0.001
        return {
            "label": "Cross-platform comparison",
            "reason": "Detected NinjaTrader vs MT5 comparison; use hybrid matching by default.",
            "matching_mode": "hybrid",
            "strict_entry_tolerance_seconds": 21600,
            "strict_exit_tolerance_seconds": 21600,
            "entry_price_tolerance": _price_tolerance_from_scale(price_scale, ratio),
            "exit_price_tolerance": _price_tolerance_from_scale(price_scale, ratio),
        }

    if {left_kind, right_kind} == {"mt5_backtest_html", "mt5_live_html"}:
        return {
            "label": "MT5 backtest vs live",
            "reason": "Detected MT5 tester report vs MT5 account-history report; match primarily by lifecycle timing.",
            "matching_mode": "time",
            "strict_entry_tolerance_seconds": 21600,
            "strict_exit_tolerance_seconds": 21600,
            "entry_price_tolerance": 0.0,
            "exit_price_tolerance": 0.0,
        }

    if any(token in names for token in backtest_tokens) and not any(token in names for token in live_tokens):
        return {
            "label": "Backtest comparison",
            "reason": "Detected backtest-style filenames.",
            "matching_mode": "time",
            "strict_entry_tolerance_seconds": 14400,
            "strict_exit_tolerance_seconds": 14400,
            "entry_price_tolerance": 0.0,
            "exit_price_tolerance": 0.0,
        }

    return {
        "label": "General comparison",
        "reason": "Using balanced defaults for a generic comparison.",
        "matching_mode": "hybrid",
        "strict_entry_tolerance_seconds": 3600,
        "strict_exit_tolerance_seconds": 3600,
        "entry_price_tolerance": _price_tolerance_from_scale(price_scale, 0.001),
        "exit_price_tolerance": _price_tolerance_from_scale(price_scale, 0.001),
    }


def _suggest_mt5_timezone(left_kind: str | None, right_kind: str | None, left_filename: str, right_filename: str) -> str:
    names = f"{left_filename} {right_filename}".lower()
    if any(kind in {"mt5_xlsx", "mt5_backtest_html", "mt5_live_html"} for kind in {left_kind, right_kind}):
        if "backtest" in names or "actual" in names or "history" in names or "mt " in names:
            return MT5_BROKER_DST_TZ
    return "UTC"


def parse_symbol_map(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        left, right = line.split("=", 1)
        mapping[left.strip()] = right.strip().upper()
    return mapping or DEFAULT_SYMBOL_MAP


def parse_aud_rate_map(text: str) -> dict[str, float]:
    mapping: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        left, right = line.split("=", 1)
        month_key = left.strip()
        try:
            mapping[month_key] = float(right.strip())
        except ValueError:
            continue
    return mapping


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Sheet1") -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buffer.getvalue()


def _safe_name_part(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(text))
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "file"


def _comparison_download_prefix(data: dict) -> str:
    settings = data.get("settings", {}) if isinstance(data, dict) else {}
    left_name = Path(str(settings.get("left_filename", "source"))).stem
    right_name = Path(str(settings.get("right_filename", "target"))).stem
    return f"{_safe_name_part(left_name)}_vs_{_safe_name_part(right_name)}"


def hist_series(series: pd.Series, bins: int = 25) -> pd.DataFrame:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return pd.DataFrame({"count": []})
    counts, edges = np.histogram(clean, bins=bins)
    labels = [f"{edges[i]:.2f}..{edges[i + 1]:.2f}" for i in range(len(edges) - 1)]
    return pd.DataFrame({"count": counts}, index=labels)


def _load_normalized(input_obj, kind: str, symbol_map: dict[str, str], mt5_timezone: str, filename: str = "", ctrader_timezone: str = "UTC") -> pd.DataFrame:
    if hasattr(input_obj, "seek"):
        try:
            input_obj.seek(0)
        except Exception:
            pass
    if kind == "nt_csv":
        return load_ninjatrader_csv(input_obj, symbol_map)
    if kind == "nt_xlsx":
        return load_ninjatrader_xlsx(input_obj, symbol_map, filename=filename)
    if kind == "mt5_xlsx":
        return load_mt5_xlsx(input_obj, symbol_map, mt5_timezone=mt5_timezone, ctrader_timezone=ctrader_timezone)
    if kind == "mt5_backtest_html":
        return load_mt5_html(input_obj, symbol_map, mt5_timezone=mt5_timezone, source_label="mt5_backtest")
    if kind == "mt5_live_html":
        return load_mt5_html(input_obj, symbol_map, mt5_timezone=mt5_timezone, source_label="mt5_live")
    raise ValueError(f"Unsupported loader kind: {kind}")


def _entry_range(df: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if df.empty or "entry_time_utc" not in df.columns:
        return None, None
    t = pd.to_datetime(df["entry_time_utc"], errors="coerce").dropna()
    if t.empty:
        return None, None
    return t.min(), t.max()


def _apply_entry_date_filter(df: pd.DataFrame, start_date: pd.Timestamp | None, end_date: pd.Timestamp | None) -> pd.DataFrame:
    if df.empty or start_date is None or end_date is None:
        return df
    t = pd.to_datetime(df.get("entry_time_utc"), errors="coerce")
    mask = t.between(start_date, end_date, inclusive="both")
    return df[mask].copy()


def _labels_from_data(data: dict) -> tuple[str, str]:
    settings = data.get("settings", {}) if isinstance(data, dict) else {}
    left = settings.get("left_label")
    right = settings.get("right_label")
    if left and right:
        return str(left), str(right)

    nt_sources = set(data.get("nt_norm", pd.DataFrame()).get("source", pd.Series(dtype=str)).astype(str).str.lower().unique())
    mt5_sources = set(data.get("mt5_norm", pd.DataFrame()).get("source", pd.Series(dtype=str)).astype(str).str.lower().unique())

    if "mt5_backtest" in nt_sources and "mt5_live" in mt5_sources:
        return "Source", "Target"
    return "Source", "Target"


def compute_metrics(data: dict[str, pd.DataFrame]) -> dict[str, float]:
    matched = data["matched"]
    unmatched_source = data.get("unmatched_source", data.get("unmatched_nt", pd.DataFrame()))
    unmatched_target = data.get("unmatched_target", data.get("unmatched_mt5", pd.DataFrame()))
    src_status = unmatched_source.get("match_status", pd.Series(dtype=str)).astype(str)
    tgt_status = unmatched_target.get("match_status", pd.Series(dtype=str)).astype(str)

    entry_diff = pd.to_numeric(matched.get("model_to_live_entry_difference_pts"), errors="coerce") if not matched.empty else pd.Series(dtype=float)
    exit_diff = pd.to_numeric(matched.get("model_to_live_exit_difference_pts"), errors="coerce") if not matched.empty else pd.Series(dtype=float)
    # Source/Target aliases over legacy nt/mt5 column names.
    src_points = pd.to_numeric(matched.get("nt_points"), errors="coerce") if not matched.empty else pd.Series(dtype=float)
    tgt_points = pd.to_numeric(matched.get("mt5_points"), errors="coerce") if not matched.empty else pd.Series(dtype=float)

    return {
        "matched_count": int((matched.get("match_status", pd.Series(dtype=str)).astype(str) == "MATCHED_STRICT").sum()) if not matched.empty else 0,
        "timing_mismatch_count": int(((src_status == "TIMING_MISMATCH") | (src_status == "EXIT_TIME_MISMATCH")).sum()),
        "missed_left": int((src_status == "UNMATCHED_SOURCE").sum()),
        "missed_right": int((tgt_status == "UNMATCHED_TARGET").sum()),
        "avg_entry_diff": float(entry_diff.mean()) if not entry_diff.empty else 0.0,
        "avg_abs_entry_diff": float(entry_diff.abs().mean()) if not entry_diff.empty else 0.0,
        "avg_exit_diff": float(exit_diff.mean()) if not exit_diff.empty else 0.0,
        "avg_abs_exit_diff": float(exit_diff.abs().mean()) if not exit_diff.empty else 0.0,
        "total_left_points": float(src_points.sum()) if not src_points.empty else 0.0,
        "total_right_points": float(tgt_points.sum()) if not tgt_points.empty else 0.0,
        "total_points_delta": float((tgt_points - src_points).sum()) if not src_points.empty else 0.0,
    }


def run_comparison(
    left_input,
    right_input,
    left_filename: str,
    right_filename: str,
    left_kind: str,
    right_kind: str,
    left_label: str,
    right_label: str,
    symbol_map: dict[str, str],
    mt5_timezone: str,
    ctrader_timezone: str,
    matching_mode: str,
    quantity_mode: str,
    quantity_factor: float,
    nt_entry_shift_seconds: int,
    strict_entry_tolerance_seconds: int,
    strict_exit_tolerance_seconds: int,
    entry_price_tolerance: float,
    exit_price_tolerance: float,
    use_date_filter: bool,
    date_filter_start: pd.Timestamp | None,
    date_filter_end: pd.Timestamp | None,
    settings: dict,
) -> dict[str, pd.DataFrame | str]:
    left_norm = _load_normalized(left_input, left_kind, symbol_map, mt5_timezone, filename=left_filename, ctrader_timezone=ctrader_timezone)
    right_norm = _load_normalized(right_input, right_kind, symbol_map, mt5_timezone, filename=right_filename, ctrader_timezone=ctrader_timezone)

    left_strategy_tag = _infer_strategy_tag_from_filename(left_filename)
    right_strategy_tag = _infer_strategy_tag_from_filename(right_filename)

    if right_kind == "mt5_live_html" and "strategy_tag" in right_norm.columns and left_strategy_tag:
        filtered = right_norm[right_norm["strategy_tag"].astype(str).str.upper() == left_strategy_tag].copy()
        if not filtered.empty:
            right_norm = filtered
            settings["right_strategy_tag_filter"] = left_strategy_tag

    if left_kind == "mt5_live_html" and "strategy_tag" in left_norm.columns and right_strategy_tag:
        filtered = left_norm[left_norm["strategy_tag"].astype(str).str.upper() == right_strategy_tag].copy()
        if not filtered.empty:
            left_norm = filtered
            settings["left_strategy_tag_filter"] = right_strategy_tag

    manual_strategy_filter = str(settings.get("strategy_tag_filter", "All")).strip().upper()
    if manual_strategy_filter and manual_strategy_filter != "ALL":
        if right_kind == "mt5_live_html" and "strategy_tag" in right_norm.columns:
            filtered = right_norm[right_norm["strategy_tag"].astype(str).str.upper() == manual_strategy_filter].copy()
            if not filtered.empty:
                right_norm = filtered
                settings["right_strategy_tag_filter"] = manual_strategy_filter
        if left_kind == "mt5_live_html" and "strategy_tag" in left_norm.columns:
            filtered = left_norm[left_norm["strategy_tag"].astype(str).str.upper() == manual_strategy_filter].copy()
            if not filtered.empty:
                left_norm = filtered
                settings["left_strategy_tag_filter"] = manual_strategy_filter

    if use_date_filter and date_filter_start is not None and date_filter_end is not None:
        left_norm = _apply_entry_date_filter(left_norm, date_filter_start, date_filter_end)
        right_norm = _apply_entry_date_filter(right_norm, date_filter_start, date_filter_end)

    # Align MT5 HTML source/target comparisons to overlapping date window.
    if left_kind == "mt5_backtest_html" and right_kind == "mt5_live_html" and not left_norm.empty and not right_norm.empty:
        left_start = pd.to_datetime(left_norm["entry_time_utc"], errors="coerce").min()
        left_end = pd.to_datetime(left_norm["exit_time_utc"], errors="coerce").max()
        right_start = pd.to_datetime(right_norm["entry_time_utc"], errors="coerce").min()
        right_end = pd.to_datetime(right_norm["exit_time_utc"], errors="coerce").max()

        overlap_start = max(left_start, right_start)
        overlap_end = min(left_end, right_end)
        if pd.isna(overlap_start) or pd.isna(overlap_end) or overlap_start > overlap_end:
            raise ValueError(
                "No overlapping date range between Source and Target MT5 trades. "
                f"Source window: {left_start}..{left_end}; Target window: {right_start}..{right_end}"
            )

        left_norm = left_norm[
            pd.to_datetime(left_norm["entry_time_utc"], errors="coerce").between(overlap_start, overlap_end, inclusive="both")
        ].copy()
        right_norm = right_norm[
            pd.to_datetime(right_norm["entry_time_utc"], errors="coerce").between(overlap_start, overlap_end, inclusive="both")
        ].copy()
        settings["aligned_overlap_start"] = str(overlap_start)
        settings["aligned_overlap_end"] = str(overlap_end)

    config = MatchConfig(
        matching_mode=matching_mode,
        quantity_mode=quantity_mode,
        quantity_factor=quantity_factor,
        nt_entry_shift_seconds=nt_entry_shift_seconds,
        strict_entry_tolerance_seconds=strict_entry_tolerance_seconds,
        strict_exit_tolerance_seconds=strict_exit_tolerance_seconds,
        entry_price_tolerance=entry_price_tolerance,
        exit_price_tolerance=exit_price_tolerance,
    )

    matched, unmatched_left, unmatched_right = match_trades(left_norm, right_norm, config)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    matched.to_csv(REPORTS_DIR / "matched.csv", index=False)
    unmatched_left.to_csv(REPORTS_DIR / "unmatched_nt.csv", index=False)
    unmatched_right.to_csv(REPORTS_DIR / "unmatched_mt5.csv", index=False)

    run_id = str(uuid4())
    created_at = pd.Timestamp.utcnow().tz_localize(None)
    try:
        persist_run(
            run_id=run_id,
            created_at=created_at,
            nt_filename=left_filename,
            mt5_filename=right_filename,
            settings=settings,
            nt_df=left_norm,
            mt5_df=right_norm,
            matches_df=matched,
            unmatched_nt_df=unmatched_left,
            unmatched_mt5_df=unmatched_right,
        )
    except Exception:
        if os.getenv("PTA_DB_READ_ONLY", "0").strip().lower() not in {"1", "true", "yes"}:
            raise

    return {
        "run_id": run_id,
        "source_norm": left_norm,
        "target_norm": right_norm,
        "nt_norm": left_norm,
        "mt5_norm": right_norm,
        "matched": matched,
        "unmatched_source": unmatched_left,
        "unmatched_target": unmatched_right,
        "unmatched_nt": unmatched_left,
        "unmatched_mt5": unmatched_right,
        "settings": settings,
        "left_label": left_label,
        "right_label": right_label,
    }


def render_export_daily_report(data: dict, mt5_timezone: str) -> None:
    st.subheader("Daily Report Export")
    if st.button("Export Daily Report (XLSX)", key="export_daily_report_btn"):
        try:
            settings = data.get("settings", {}) if isinstance(data, dict) else {}
            mode_label = settings.get("matching_mode_label", settings.get("algorithm", "Time"))
            out_path, out_bytes = generate_daily_report_xlsx(data=data, mt5_timezone=mt5_timezone, matching_mode_label=str(mode_label))
            st.session_state["daily_report_bytes"] = out_bytes
            st.session_state["daily_report_name"] = out_path.name
            st.success(f"Daily report generated: {out_path}")
        except Exception as exc:
            st.exception(exc)

    if "daily_report_bytes" in st.session_state:
        st.download_button(
            "Download Daily Report (XLSX)",
            data=st.session_state["daily_report_bytes"],
            file_name=st.session_state.get("daily_report_name", "Source_vs_Target_Daily_Report.xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def render_overview(data: dict[str, pd.DataFrame]) -> None:
    left_label, right_label = _labels_from_data(data)
    metrics = compute_metrics(data)
    settings = data.get("settings", {}) if isinstance(data, dict) else {}
    if settings.get("use_date_filter"):
        start = settings.get("date_filter_start")
        end = settings.get("date_filter_end")
        if start and end:
            st.caption(f"Date range applied: {start}  {end}")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Matched", int(metrics["matched_count"]))
    c2.metric("Timing mismatches", int(metrics["timing_mismatch_count"]))
    c3.metric(f"Unmatched {left_label}", int(metrics["missed_left"]))
    c4.metric(f"Unmatched {right_label}", int(metrics["missed_right"]))
    c5.metric("Points Delta (Target-Source)", round(float(metrics["total_points_delta"]), 2))

    summary = pd.DataFrame(
        [
            {"metric": "Strict matched count", "value": int(metrics["matched_count"])},
            {"metric": "Timing mismatches count", "value": int(metrics["timing_mismatch_count"])},
            {"metric": f"Unmatched {left_label}", "value": int(metrics["missed_left"])},
            {"metric": f"Unmatched {right_label}", "value": int(metrics["missed_right"])},
            {"metric": "Avg Target-Source Entry Difference (pts)", "value": round(float(metrics["avg_entry_diff"]), 4)},
            {"metric": "Avg Abs Entry Difference (pts)", "value": round(float(metrics["avg_abs_entry_diff"]), 4)},
            {"metric": "Avg Target-Source Exit Difference (pts)", "value": round(float(metrics["avg_exit_diff"]), 4)},
            {"metric": "Avg Abs Exit Difference (pts)", "value": round(float(metrics["avg_abs_exit_diff"]), 4)},
            {"metric": f"Total {left_label} Points (matched)", "value": round(float(metrics["total_left_points"]), 2)},
            {"metric": f"Total {right_label} Points (matched)", "value": round(float(metrics["total_right_points"]), 2)},
            {"metric": "Total Points Delta (Target-Source)", "value": round(float(metrics["total_points_delta"]), 2)},
        ]
    )
    st.subheader("Completed Trade Comparison")
    st.dataframe(summary, width="stretch")

    matched = data["matched"]
    if matched.empty:
        st.info("No matched trades.")
        return

    st.subheader("Target-Source Entry Difference (pts)")
    st.bar_chart(hist_series(matched["model_to_live_entry_difference_pts"]))

    st.subheader("Target-Source Exit Difference (pts)")
    st.bar_chart(hist_series(matched["model_to_live_exit_difference_pts"]))


def render_matched(data: dict[str, pd.DataFrame]) -> None:
    matched = data["matched"].copy()
    st.subheader("Matched Trades (Source vs Target)")
    if matched.empty:
        st.info("No matched trades.")
        return
    download_prefix = _comparison_download_prefix(data)

    symbol_filter = st.multiselect("Symbol", sorted(matched["symbol_norm"].dropna().unique().tolist()))
    side_filter = st.multiselect("Side", sorted(matched["side"].dropna().unique().tolist()))
    diff_filter = st.number_input("Min absolute Target-Source Difference", min_value=0.0, value=0.0, step=0.1)

    filtered = matched
    if symbol_filter:
        filtered = filtered[filtered["symbol_norm"].isin(symbol_filter)]
    if side_filter:
        filtered = filtered[filtered["side"].isin(side_filter)]
    if diff_filter > 0:
        filtered = filtered[
            (filtered["model_to_live_entry_difference_pts"].abs() >= diff_filter)
            | (filtered["model_to_live_exit_difference_pts"].abs() >= diff_filter)
        ]

    settings = data.get("settings", {}) if isinstance(data, dict) else {}
    aud_rate_map = settings.get("aud_rate_map", {})
    if aud_rate_map:
        tgt_month = pd.to_datetime(filtered.get("mt5_entry_time_utc"), errors="coerce").dt.to_period("M").astype("string")
        filtered["tgt_month"] = tgt_month
        filtered["aud_monthly_rate"] = pd.to_numeric(tgt_month.map(aud_rate_map), errors="coerce")
        filtered["mt5_actual_cost_aud"] = (
            pd.to_numeric(filtered.get("points_delta"), errors="coerce")
            * pd.to_numeric(filtered.get("mt5_qty"), errors="coerce")
            * pd.to_numeric(filtered.get("aud_monthly_rate"), errors="coerce")
        )

    cols = [
        "symbol_norm",
        "side",
        "nt_trade_id",
        "mt5_trade_id",
        "nt_entry_time_utc",
        "mt5_entry_time_utc",
        "nt_entry_price",
        "mt5_entry_price",
        "model_to_live_entry_difference_pts",
        "entry_price_delta_abs",
        "entry_time_delta_seconds",
        "nt_exit_time_utc",
        "mt5_exit_time_utc",
        "nt_exit_price",
        "mt5_exit_price",
        "model_to_live_exit_difference_pts",
        "exit_price_delta_abs",
        "exit_time_delta_seconds",
        "nt_points",
        "mt5_points",
        "points_delta",
        "tgt_month",
        "aud_monthly_rate",
        "mt5_actual_cost_aud",
    ]
    show_cols = [c for c in cols if c in filtered.columns]
    display = filtered[show_cols].rename(
        columns={
            "nt_trade_id": "src_trade_id",
            "mt5_trade_id": "tgt_trade_id",
            "nt_entry_time_utc": "src_entry_time",
            "mt5_entry_time_utc": "tgt_entry_time",
            "nt_entry_price": "src_entry_price",
            "mt5_entry_price": "tgt_entry_price",
            "nt_exit_time_utc": "src_exit_time",
            "mt5_exit_time_utc": "tgt_exit_time",
            "nt_exit_price": "src_exit_price",
            "mt5_exit_price": "tgt_exit_price",
            "nt_points": "src_points",
            "mt5_points": "tgt_points",
            "model_to_live_entry_difference_pts": "entry_slip_pts",
            "entry_price_delta_abs": "entry_price_abs_delta",
            "entry_time_delta_seconds": "entry_time_delta_s",
            "model_to_live_exit_difference_pts": "exit_slip_pts",
            "exit_price_delta_abs": "exit_price_abs_delta",
            "exit_time_delta_seconds": "exit_time_delta_s",
            "points_delta": "points_delta_target_minus_source",
        }
    )
    st.dataframe(display, width="stretch")
    st.download_button(
        "Download matched CSV",
        to_csv_bytes(filtered[show_cols]),
        f"{download_prefix}_matched_filtered.csv",
        "text/csv",
    )
    st.download_button(
        "Download matched XLSX",
        to_xlsx_bytes(display, sheet_name="matched_trades"),
        f"{download_prefix}_matched_filtered.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def render_trade_by_trade(data: dict[str, pd.DataFrame]) -> None:
    matched = data["matched"].copy()
    st.subheader("Trade-by-Trade (Matched Pairs)")
    if matched.empty:
        st.info("No matched trades.")
        return
    download_prefix = _comparison_download_prefix(data)

    view = pd.DataFrame(
        {
            "src_entry_time": matched.get("nt_entry_time_utc"),
            "src_type": matched.get("side"),
            "src_symbol": matched.get("symbol_norm"),
            "src_volume": matched.get("nt_qty"),
            "src_entry_price": matched.get("nt_entry_price"),
            "src_exit_time": matched.get("nt_exit_time_utc"),
            "src_exit_price": matched.get("nt_exit_price"),
            "src_pnl_points": matched.get("nt_points"),
            "tgt_entry_time": matched.get("mt5_entry_time_utc"),
            "tgt_type": matched.get("side"),
            "tgt_symbol": matched.get("symbol_norm"),
            "tgt_volume": matched.get("mt5_qty"),
            "tgt_entry_price": matched.get("mt5_entry_price"),
            "tgt_exit_time": matched.get("mt5_exit_time_utc"),
            "tgt_exit_price": matched.get("mt5_exit_price"),
            "tgt_pnl_points": matched.get("mt5_points"),
            # Positive entry/exit slip means the target/MT5 fill was better than the source fill.
            "entry_slip_pts": pd.to_numeric(matched.get("model_to_live_entry_difference_pts"), errors="coerce"),
            "exit_slip_pts": pd.to_numeric(matched.get("model_to_live_exit_difference_pts"), errors="coerce"),
        }
    )
    if "points_delta" in matched.columns:
        view["total_slip_pts"] = pd.to_numeric(matched.get("points_delta"), errors="coerce")
    else:
        view["total_slip_pts"] = pd.to_numeric(view["entry_slip_pts"], errors="coerce").fillna(0.0) + pd.to_numeric(
            view["exit_slip_pts"], errors="coerce"
        ).fillna(0.0)

    settings = data.get("settings", {}) if isinstance(data, dict) else {}
    aud_rate_map = settings.get("aud_rate_map", {})
    tgt_month = pd.to_datetime(view.get("tgt_entry_time"), errors="coerce").dt.to_period("M").astype("string")
    view["tgt_month"] = tgt_month
    if aud_rate_map:
        aud_rate_series = tgt_month.map(aud_rate_map)
        view["aud_monthly_rate"] = pd.to_numeric(aud_rate_series, errors="coerce")
        view["mt5_actual_cost_aud"] = (
            pd.to_numeric(view["total_slip_pts"], errors="coerce")
            * pd.to_numeric(view["tgt_volume"], errors="coerce")
            * pd.to_numeric(view["aud_monthly_rate"], errors="coerce")
        )

    # Keep compatibility check with existing aggregate metric.
    total_slip = float(pd.to_numeric(view["total_slip_pts"], errors="coerce").fillna(0.0).sum())
    total_metric = float(compute_metrics(data)["total_points_delta"])
    st.caption(
        f"Total slip sum: {total_slip:.6f} | Total points delta metric: {total_metric:.6f} | diff={abs(total_slip - total_metric):.6f}"
    )

    st.dataframe(view, width="stretch")
    st.download_button(
        "Export Matched Trades (CSV)",
        data=to_csv_bytes(view),
        file_name=f"{download_prefix}_trade_by_trade_matched.csv",
        mime="text/csv",
    )
    st.download_button(
        "Export Matched Trades (XLSX)",
        data=to_xlsx_bytes(view, sheet_name="trade_by_trade"),
        file_name=f"{download_prefix}_trade_by_trade_matched.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def render_unmatched(data: dict[str, pd.DataFrame]) -> None:
    left_label, right_label = _labels_from_data(data)
    unmatched_source = data.get("unmatched_source", data.get("unmatched_nt", pd.DataFrame()))
    unmatched_target = data.get("unmatched_target", data.get("unmatched_mt5", pd.DataFrame()))
    download_prefix = _comparison_download_prefix(data)

    combined_unmatched = pd.concat(
        [
            unmatched_source.assign(unmatched_side=left_label),
            unmatched_target.assign(unmatched_side=right_label),
        ],
        ignore_index=True,
        sort=False,
    )

    st.subheader(f"{left_label} Unmatched")
    st.dataframe(unmatched_source, width="stretch")
    st.download_button(
        f"Download {left_label} unmatched CSV",
        to_csv_bytes(unmatched_source),
        f"{download_prefix}_unmatched_{_safe_name_part(left_label.lower())}.csv",
        "text/csv",
    )
    st.download_button(
        f"Download {left_label} unmatched XLSX",
        to_xlsx_bytes(unmatched_source, sheet_name=f"{_safe_name_part(left_label.lower())[:25]}_unmatched"),
        f"{download_prefix}_unmatched_{_safe_name_part(left_label.lower())}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.subheader(f"{right_label} Unmatched")
    st.dataframe(unmatched_target, width="stretch")
    st.download_button(
        f"Download {right_label} unmatched CSV",
        to_csv_bytes(unmatched_target),
        f"{download_prefix}_unmatched_{_safe_name_part(right_label.lower())}.csv",
        "text/csv",
    )
    st.download_button(
        f"Download {right_label} unmatched XLSX",
        to_xlsx_bytes(unmatched_target, sheet_name=f"{_safe_name_part(right_label.lower())[:25]}_unmatched"),
        f"{download_prefix}_unmatched_{_safe_name_part(right_label.lower())}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.subheader("Combined Unmatched")
    st.dataframe(combined_unmatched, width="stretch")
    st.download_button(
        "Download combined unmatched CSV",
        to_csv_bytes(combined_unmatched),
        f"{download_prefix}_unmatched_combined.csv",
        "text/csv",
    )
    st.download_button(
        "Download combined unmatched XLSX",
        to_xlsx_bytes(combined_unmatched, sheet_name="unmatched_combined"),
        f"{download_prefix}_unmatched_combined.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def render_debug(data: dict[str, pd.DataFrame]) -> None:
    left_label, right_label = _labels_from_data(data)
    source_norm = data.get("source_norm", data.get("nt_norm", pd.DataFrame()))
    target_norm = data.get("target_norm", data.get("mt5_norm", pd.DataFrame()))

    st.subheader(f"Normalized {left_label} (preview up to 510 rows)")
    st.dataframe(source_norm.head(510), width="stretch")

    st.subheader(f"Normalized {right_label} (preview up to 510 rows)")
    st.dataframe(target_norm.head(510), width="stretch")


def render_run_history() -> None:
    st.subheader("Run History")
    history = run_history()
    if history.empty:
        st.info("No runs persisted yet.")
        return

    st.dataframe(history, width="stretch")
    selected_run = st.selectbox("Select run_id", options=history["run_id"].tolist())
    if st.button("Load selected run"):
        loaded = load_run(selected_run)
        loaded["run_id"] = selected_run
        st.session_state["comparison_result"] = loaded
        st.success(f"Loaded run {selected_run}")


def render_trends() -> None:
    st.subheader("Trends")
    window = st.selectbox("Trend window", options=[7, 30], index=0)
    trends = trend_history(window)
    if trends.empty:
        st.info("No trend data yet.")
        return

    st.dataframe(trends, width="stretch")
    st.line_chart(trends.set_index("created_at")[["missed_count"]], height=180)
    st.line_chart(trends.set_index("created_at")[["avg_points_diff"]], height=180)
    st.line_chart(trends.set_index("created_at")[["profit_factor_diff"]], height=180)
    st.line_chart(trends.set_index("created_at")[["net_profit_delta"]], height=180)


def main() -> None:
    st.set_page_config(page_title="Post-Trade Comparison Dashboard", layout="wide")
    st.title("Post-Trade Comparison Dashboard")
    left_label = "Source"
    right_label = "Target"
    left_kind: str | None = None
    right_kind: str | None = None

    st.sidebar.header("Data Source")
    source_mode = st.sidebar.radio("Input mode", options=["Upload files", "Load from local paths"], index=0)

    left_file = None
    right_file = None
    left_filename = ""
    right_filename = ""

    if source_mode == "Upload files":
        left_file = st.sidebar.file_uploader(
            f"{left_label} file",
            type=SUPPORTED_UPLOAD_TYPES,
            help="Upload csv, xlsx, html, or htm.",
            key="upload_source_file",
        )
        right_file = st.sidebar.file_uploader(
            f"{right_label} file",
            type=SUPPORTED_UPLOAD_TYPES,
            help="Upload csv, xlsx, html, or htm.",
            key="upload_target_file",
        )
        if left_file is not None:
            left_filename = left_file.name
        if right_file is not None:
            right_filename = right_file.name
    else:
        key_prefix = "generic_compare"
        left_path = st.sidebar.text_input(f"{left_label} path", value=DEFAULT_LEFT_PATH)
        right_path = st.sidebar.text_input(f"{right_label} path", value=DEFAULT_RIGHT_PATH)
        if st.sidebar.button("Load files"):
            left_p = Path(left_path)
            right_p = Path(right_path)
            if not left_p.exists() or not right_p.exists():
                st.sidebar.error("One or both files do not exist.")
            else:
                st.session_state[f"{key_prefix}_left_path"] = str(left_p)
                st.session_state[f"{key_prefix}_right_path"] = str(right_p)
                st.session_state[f"{key_prefix}_left_mtime"] = pd.Timestamp(left_p.stat().st_mtime, unit="s")
                st.session_state[f"{key_prefix}_right_mtime"] = pd.Timestamp(right_p.stat().st_mtime, unit="s")
                st.sidebar.success("Local files loaded.")

        left_key = f"{key_prefix}_left_path"
        right_key = f"{key_prefix}_right_path"
        if left_key in st.session_state and right_key in st.session_state:
            st.sidebar.caption(f"{left_label} last modified: {st.session_state[f'{key_prefix}_left_mtime']}")
            st.sidebar.caption(f"{right_label} last modified: {st.session_state[f'{key_prefix}_right_mtime']}")
            left_file = st.session_state[left_key]
            right_file = st.session_state[right_key]
            left_filename = Path(left_file).name
            right_filename = Path(right_file).name

    left_kind_error: str | None = None
    right_kind_error: str | None = None
    if left_filename:
        try:
            left_kind = _infer_kind(left_file, left_filename, "source")
        except ValueError as exc:
            left_kind_error = str(exc)
    if right_filename:
        try:
            right_kind = _infer_kind(right_file, right_filename, "target")
        except ValueError as exc:
            right_kind_error = str(exc)

    if left_kind:
        st.sidebar.caption(f"{left_label} detected type: {_kind_label(left_kind)}")
    if right_kind:
        st.sidebar.caption(f"{right_label} detected type: {_kind_label(right_kind)}")
    if left_kind_error:
        st.sidebar.error(left_kind_error)
    if right_kind_error:
        st.sidebar.error(right_kind_error)

    st.sidebar.header("Settings")
    tz_base = [MT5_BROKER_DST_TZ, "UTC", "Etc/GMT-2", "Etc/GMT-3"]
    tz_choices = tz_base + sorted([tz for tz in available_timezones() if tz not in set(tz_base)])
    mt5_timezone = _suggest_mt5_timezone(left_kind, right_kind, left_filename, right_filename)
    ctrader_timezone = "UTC"
    nt_entry_shift_seconds = 0
    quantity_mode = "ignore for matching"
    quantity_factor = 1.0

    symbol_map_default = "\n".join(f"{k}={v}" for k, v in DEFAULT_SYMBOL_MAP.items())
    symbol_map_text = symbol_map_default
    symbol_map = parse_symbol_map(symbol_map_text)
    aud_rate_default = "\n".join(f"{k}={v}" for k, v in DEFAULT_AUD_RATE_MAP.items())
    aud_rate_map = DEFAULT_AUD_RATE_MAP.copy()
    strategy_tag_filter = "All"

    left_columns = set(_peek_table_columns(left_file, left_filename)) if left_file is not None and left_filename else set()
    right_columns = set(_peek_table_columns(right_file, right_filename)) if right_file is not None and right_filename else set()
    ctrader_detected = _is_ctrader_xlsx_columns(left_columns) or _is_ctrader_xlsx_columns(right_columns)

    available_min: pd.Timestamp | None = None
    available_max: pd.Timestamp | None = None
    left_preview = pd.DataFrame()
    right_preview = pd.DataFrame()
    if left_file is not None and left_kind is not None:
        try:
            left_preview = _load_normalized(left_file, left_kind, symbol_map, mt5_timezone, filename=left_filename, ctrader_timezone=ctrader_timezone)
            lmin, lmax = _entry_range(left_preview)
            available_min = lmin if available_min is None else min(available_min, lmin) if lmin is not None else available_min
            available_max = lmax if available_max is None else max(available_max, lmax) if lmax is not None else available_max
        except Exception:
            left_preview = pd.DataFrame()
    if right_file is not None and right_kind is not None:
        try:
            right_preview = _load_normalized(right_file, right_kind, symbol_map, mt5_timezone, filename=right_filename, ctrader_timezone=ctrader_timezone)
            rmin, rmax = _entry_range(right_preview)
            available_min = rmin if available_min is None else min(available_min, rmin) if rmin is not None else available_min
            available_max = rmax if available_max is None else max(available_max, rmax) if rmax is not None else available_max
        except Exception:
            right_preview = pd.DataFrame()

    preset = _suggest_matching_preset(left_kind, right_kind, left_filename, right_filename, left_columns, right_columns, left_preview, right_preview)
    use_auto_matching_preset = st.sidebar.checkbox("Use recommended matching settings", value=True, key="use_auto_matching_preset")

    if use_auto_matching_preset:
        matching_mode = str(preset["matching_mode"])
        strict_entry_tolerance_seconds = int(preset["strict_entry_tolerance_seconds"])
        strict_exit_tolerance_seconds = int(preset["strict_exit_tolerance_seconds"])
        entry_price_tolerance = float(preset["entry_price_tolerance"])
        exit_price_tolerance = float(preset["exit_price_tolerance"])
        st.sidebar.caption(
            f"Recommended: {preset['label']} | mode={matching_mode}, entry_tol={strict_entry_tolerance_seconds}s, "
            f"exit_tol={strict_exit_tolerance_seconds}s, entry_px_tol={entry_price_tolerance}, exit_px_tol={exit_price_tolerance}"
        )
        st.sidebar.caption(str(preset["reason"]))
    else:
        with st.sidebar.expander("Manual Matching Settings", expanded=True):
            matching_mode = st.selectbox("Matching mode", options=["time", "hybrid", "price"], index=1)
            strict_entry_tolerance_seconds = st.number_input("Entry tolerance (seconds)", min_value=0, value=20, step=1)
            strict_exit_tolerance_seconds = st.number_input("Exit tolerance (seconds)", min_value=0, value=20, step=1)
            entry_price_tolerance = st.number_input("Entry price tolerance", min_value=0.0, value=0.0, step=0.01, format="%.5f")
            exit_price_tolerance = st.number_input("Exit price tolerance", min_value=0.0, value=0.0, step=0.01, format="%.5f")

    with st.sidebar.expander("Advanced Import Settings", expanded=False):
        mt5_timezone = st.selectbox(
            "MT5 timezone",
            options=tz_choices,
            index=(tz_choices.index(mt5_timezone) if mt5_timezone in tz_choices else 0),
            format_func=lambda tz: MT5_TIMEZONE_LABELS.get(tz, tz),
            help="Use the broker DST option for MT5 servers that run UTC+2 in winter and UTC+3 in summer.",
        )
        if ctrader_detected:
            ctrader_timezone = st.selectbox(
                "cTrader timezone",
                options=tz_choices,
                index=(tz_choices.index("UTC") if "UTC" in tz_choices else 0),
                format_func=lambda tz: MT5_TIMEZONE_LABELS.get(tz, tz),
                help="Use the timezone the cTrader export timestamps are already expressed in.",
            )
        if left_kind in {"nt_csv", "nt_xlsx"}:
            st.text_input("NinjaTrader timezone (fixed)", value=NT_TIMEZONE, disabled=True)
            nt_entry_shift_seconds = st.number_input(
                "Source entry time shift (seconds)",
                min_value=-86400,
                max_value=86400,
                value=0,
                step=1,
            )
            quantity_mode = st.selectbox(
                "Quantity matching mode",
                options=["ignore for matching", "convert using factor"],
                index=0,
            )
            if quantity_mode == "convert using factor":
                quantity_factor = st.number_input("Quantity conversion factor", min_value=0.000001, value=1.0)
        strategy_tag_options = _strategy_tag_options(left_preview, right_preview)
        if strategy_tag_options:
            strategy_choices = ["All"] + strategy_tag_options
            default_strategy_tag = _default_strategy_tag_filter(left_kind, right_kind, strategy_tag_options, left_filename, right_filename)
            strategy_tag_filter = st.selectbox(
                "MT5 strategy/comment filter",
                options=strategy_choices,
                index=strategy_choices.index(default_strategy_tag) if default_strategy_tag in strategy_choices else 0,
                help="Filters MT5 HTML account-history rows by the hidden strategy/comment tag before matching.",
            )
        symbol_map_text = st.text_area("Symbol mapping (pattern=target, one per line)", value=symbol_map_default, height=100)
        symbol_map = parse_symbol_map(symbol_map_text)
        aud_rate_text = st.text_area("AUD monthly rates (YYYY-MM=rate)", value=aud_rate_default, height=100)
        parsed_aud_rate_map = parse_aud_rate_map(aud_rate_text)
        if parsed_aud_rate_map:
            aud_rate_map = parsed_aud_rate_map

    st.sidebar.markdown("### Date Range Filter")

    today = pd.Timestamp.utcnow().tz_localize(None).date()
    default_start = (pd.Timestamp(today) - pd.Timedelta(days=13)).date()
    default_end = today

    # Prefer source range when available.
    backtest_min, backtest_max = _entry_range(left_preview)
    if backtest_min is not None and backtest_max is not None:
        default_start = backtest_min.date()
        default_end = backtest_max.date()

    filter_defaults_sig = f"{left_kind}|{right_kind}|{left_filename}|{right_filename}|{default_start}|{default_end}"
    if st.session_state.get("date_filter_defaults_sig") != filter_defaults_sig:
        st.session_state["date_filter_start"] = default_start
        st.session_state["date_filter_end"] = default_end
        st.session_state["date_filter_defaults_sig"] = filter_defaults_sig

    use_date_filter = st.sidebar.checkbox("Use date range filter", value=True, key="use_date_filter")
    date_filter_start = st.sidebar.date_input("Start date", key="date_filter_start")
    date_filter_end = st.sidebar.date_input("End date", key="date_filter_end")

    if available_min is not None and available_max is not None:
        st.sidebar.caption(f"Available data range: {available_min.date()}  {available_max.date()}")
    else:
        st.sidebar.caption("Available data range: not detected yet")

    run_clicked = st.sidebar.button("Run comparison", type="primary")
    filter_signature = (bool(use_date_filter), str(date_filter_start), str(date_filter_end))
    prev_filter_signature = st.session_state.get("last_filter_signature")
    auto_rerun = (
        "comparison_result" in st.session_state
        and prev_filter_signature is not None
        and filter_signature != prev_filter_signature
        and left_file is not None
        and right_file is not None
    )
    should_run = run_clicked or auto_rerun

    if should_run:
        if left_file is None or right_file is None:
            st.error("Load or upload both files before running comparison.")
        elif left_kind is None or right_kind is None:
            st.error("Unsupported file type. Use csv, xlsx, html, or htm.")
        elif date_filter_start > date_filter_end:
            st.error("Start date must be on or before end date.")
        else:
            date_start_ts = pd.Timestamp(date_filter_start)
            date_end_ts = pd.Timestamp(date_filter_end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            comparison_kind = f"{left_kind}_vs_{right_kind}"
            settings = {
                "algorithm": "Matching",
                "matching_mode": matching_mode,
                "matching_mode_label": matching_mode.title(),
                "comparison_kind": comparison_kind,
                "comparison_kind_label": f"{_kind_label(left_kind)} vs {_kind_label(right_kind)}",
                "left_filename": left_filename,
                "right_filename": right_filename,
                "source_mode": source_mode,
                "mt5_timezone": mt5_timezone,
                "ctrader_timezone": ctrader_timezone,
                "nt_entry_shift_seconds": int(nt_entry_shift_seconds),
                "quantity_mode": quantity_mode,
                "quantity_factor": float(quantity_factor),
                "strict_entry_tolerance_seconds": int(strict_entry_tolerance_seconds),
                "strict_exit_tolerance_seconds": int(strict_exit_tolerance_seconds),
                "entry_price_tolerance": float(entry_price_tolerance),
                "exit_price_tolerance": float(exit_price_tolerance),
                "strategy_tag_filter": strategy_tag_filter,
                "symbol_map": symbol_map,
                "aud_rate_map": aud_rate_map,
                "left_kind": left_kind,
                "right_kind": right_kind,
                "left_label": left_label,
                "right_label": right_label,
                "use_date_filter": bool(use_date_filter),
                "date_filter_start": str(date_filter_start),
                "date_filter_end": str(date_filter_end),
            }
            result = run_comparison(
                left_input=left_file,
                right_input=right_file,
                left_filename=left_filename,
                right_filename=right_filename,
                left_kind=left_kind,
                right_kind=right_kind,
                left_label=left_label,
                right_label=right_label,
                symbol_map=symbol_map,
                mt5_timezone=mt5_timezone,
                ctrader_timezone=ctrader_timezone,
                matching_mode=matching_mode,
                quantity_mode=quantity_mode,
                quantity_factor=float(quantity_factor),
                nt_entry_shift_seconds=int(nt_entry_shift_seconds),
                strict_entry_tolerance_seconds=int(strict_entry_tolerance_seconds),
                strict_exit_tolerance_seconds=int(strict_exit_tolerance_seconds),
                entry_price_tolerance=float(entry_price_tolerance),
                exit_price_tolerance=float(exit_price_tolerance),
                use_date_filter=bool(use_date_filter),
                date_filter_start=date_start_ts if use_date_filter else None,
                date_filter_end=date_end_ts if use_date_filter else None,
                settings=settings,
            )
            if use_date_filter and (result["nt_norm"].empty or result["mt5_norm"].empty):
                st.warning("No trades found in selected date range.")
            st.session_state["comparison_result"] = result
            st.session_state["last_filter_signature"] = filter_signature
            st.success(f"Comparison completed. Run ID: {result['run_id']}")

    if "comparison_result" not in st.session_state:
        st.info("Load/upload files and press 'Run comparison' to begin.")

    tabs = st.tabs(
        [
            "Overview summary",
            "Trade-by-Trade",
            "Matched trades",
            "Unmatched trades",
            "Debug / normalized data",
            "Run history",
            "Trends",
        ]
    )

    if "comparison_result" in st.session_state:
        data = st.session_state["comparison_result"]
        with tabs[0]:
            render_export_daily_report(data, mt5_timezone)
            render_overview(data)
        with tabs[2]:
            render_matched(data)
        with tabs[3]:
            render_unmatched(data)
        with tabs[1]:
            render_trade_by_trade(data)
        with tabs[4]:
            render_debug(data)

    with tabs[5]:
        render_run_history()

    with tabs[6]:
        render_trends()

    project_version = _read_installed_version()
    packaged_version = _read_packaged_version()
    display_version = project_version or packaged_version
    if display_version:
        st.caption(f"PTA version: {display_version}")
    else:
        st.caption("PTA version: development build")


if __name__ == "__main__":
    main()
