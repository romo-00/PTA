from __future__ import annotations

from pathlib import Path
from uuid import uuid4
from zoneinfo import available_timezones

import numpy as np
import os
import pandas as pd
import streamlit as st

from daily_report import generate_daily_report_xlsx
from importers.mt5_html import load_mt5_html
from importers.mt5_xlsx import load_mt5_xlsx
from importers.ninjatrader_csv import NT_TIMEZONE, load_ninjatrader_csv
from persistence import load_run, persist_run, run_history, trend_history
from trade_matching import MatchConfig, match_trades

DEFAULT_SYMBOL_MAP = {"MNQ*": "NAS100"}
REPORTS_DIR = Path("reports")

FORMAT_MT5_HTML_VS_MT5_HTML = "mt5_html_vs_mt5_html"
FORMAT_NT_CSV_VS_MT5_XLSX = "nt_csv_vs_mt5_xlsx"

FORMAT_LABELS = {
    FORMAT_MT5_HTML_VS_MT5_HTML: "Source HTML vs Target HTML (MT5 style)",
    FORMAT_NT_CSV_VS_MT5_XLSX: "Source CSV vs Target XLSX",
}


def _read_installed_version() -> str | None:
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


def parse_symbol_map(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        left, right = line.split("=", 1)
        mapping[left.strip()] = right.strip().upper()
    return mapping or DEFAULT_SYMBOL_MAP


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def hist_series(series: pd.Series, bins: int = 25) -> pd.DataFrame:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return pd.DataFrame({"count": []})
    counts, edges = np.histogram(clean, bins=bins)
    labels = [f"{edges[i]:.2f}..{edges[i + 1]:.2f}" for i in range(len(edges) - 1)]
    return pd.DataFrame({"count": counts}, index=labels)


def _load_normalized(input_obj, kind: str, symbol_map: dict[str, str], mt5_timezone: str) -> pd.DataFrame:
    if hasattr(input_obj, "seek"):
        try:
            input_obj.seek(0)
        except Exception:
            pass
    if kind == "nt_csv":
        return load_ninjatrader_csv(input_obj, symbol_map)
    if kind == "mt5_xlsx":
        return load_mt5_xlsx(input_obj, symbol_map, mt5_timezone=mt5_timezone)
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

    entry_diff = pd.to_numeric(matched.get("model_to_live_entry_difference_pts"), errors="coerce") if not matched.empty else pd.Series(dtype=float)
    exit_diff = pd.to_numeric(matched.get("model_to_live_exit_difference_pts"), errors="coerce") if not matched.empty else pd.Series(dtype=float)
    # Source/Target aliases over legacy nt/mt5 column names.
    src_points = pd.to_numeric(matched.get("nt_points"), errors="coerce") if not matched.empty else pd.Series(dtype=float)
    tgt_points = pd.to_numeric(matched.get("mt5_points"), errors="coerce") if not matched.empty else pd.Series(dtype=float)

    return {
        "matched_count": len(matched),
        "missed_left": len(unmatched_source),
        "missed_right": len(unmatched_target),
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
    quantity_mode: str,
    quantity_factor: float,
    nt_entry_shift_seconds: int,
    use_date_filter: bool,
    date_filter_start: pd.Timestamp | None,
    date_filter_end: pd.Timestamp | None,
    settings: dict,
) -> dict[str, pd.DataFrame | str]:
    left_norm = _load_normalized(left_input, left_kind, symbol_map, mt5_timezone)
    right_norm = _load_normalized(right_input, right_kind, symbol_map, mt5_timezone)

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
        quantity_mode=quantity_mode,
        quantity_factor=quantity_factor,
        nt_entry_shift_seconds=nt_entry_shift_seconds,
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
            out_path, out_bytes = generate_daily_report_xlsx(data=data, mt5_timezone=mt5_timezone, matching_mode_label="Sequential")
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

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Matched", int(metrics["matched_count"]))
    c2.metric(f"Missed {left_label}", int(metrics["missed_left"]))
    c3.metric(f"Missed {right_label}", int(metrics["missed_right"]))
    c4.metric("Points Delta (Target-Source)", round(float(metrics["total_points_delta"]), 2))

    summary = pd.DataFrame(
        [
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
    st.dataframe(summary, use_container_width=True)

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
        "nt_exit_time_utc",
        "mt5_exit_time_utc",
        "nt_exit_price",
        "mt5_exit_price",
        "model_to_live_exit_difference_pts",
        "nt_points",
        "mt5_points",
        "points_delta",
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
            "model_to_live_exit_difference_pts": "exit_slip_pts",
            "points_delta": "points_delta_target_minus_source",
        }
    )
    st.dataframe(display, use_container_width=True)
    st.download_button("Download matched CSV", to_csv_bytes(filtered[show_cols]), "matched_filtered.csv", "text/csv")


def render_trade_by_trade(data: dict[str, pd.DataFrame]) -> None:
    matched = data["matched"].copy()
    st.subheader("Trade-by-Trade (Matched Pairs)")
    if matched.empty:
        st.info("No matched trades.")
        return

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

    # Keep compatibility check with existing aggregate metric.
    total_slip = float(pd.to_numeric(view["total_slip_pts"], errors="coerce").fillna(0.0).sum())
    total_metric = float(compute_metrics(data)["total_points_delta"])
    st.caption(
        f"Total slip sum: {total_slip:.6f} | Total points delta metric: {total_metric:.6f} | diff={abs(total_slip - total_metric):.6f}"
    )

    st.dataframe(view, use_container_width=True)
    st.download_button(
        "Export Matched Trades (CSV)",
        data=to_csv_bytes(view),
        file_name="trade_by_trade_matched.csv",
        mime="text/csv",
    )


def render_unmatched(data: dict[str, pd.DataFrame]) -> None:
    left_label, right_label = _labels_from_data(data)
    unmatched_source = data.get("unmatched_source", data.get("unmatched_nt", pd.DataFrame()))
    unmatched_target = data.get("unmatched_target", data.get("unmatched_mt5", pd.DataFrame()))

    st.subheader(f"{left_label} Unmatched")
    st.dataframe(unmatched_source, use_container_width=True)
    st.download_button(
        f"Download {left_label} unmatched CSV",
        to_csv_bytes(unmatched_source),
        "unmatched_left_filtered.csv",
        "text/csv",
    )

    st.subheader(f"{right_label} Unmatched")
    st.dataframe(unmatched_target, use_container_width=True)
    st.download_button(
        f"Download {right_label} unmatched CSV",
        to_csv_bytes(unmatched_target),
        "unmatched_right_filtered.csv",
        "text/csv",
    )


def render_debug(data: dict[str, pd.DataFrame]) -> None:
    left_label, right_label = _labels_from_data(data)
    source_norm = data.get("source_norm", data.get("nt_norm", pd.DataFrame()))
    target_norm = data.get("target_norm", data.get("mt5_norm", pd.DataFrame()))

    st.subheader(f"Normalized {left_label} (preview up to 510 rows)")
    st.dataframe(source_norm.head(510), use_container_width=True)

    st.subheader(f"Normalized {right_label} (preview up to 510 rows)")
    st.dataframe(target_norm.head(510), use_container_width=True)


def render_run_history() -> None:
    st.subheader("Run History")
    history = run_history()
    if history.empty:
        st.info("No runs persisted yet.")
        return

    st.dataframe(history, use_container_width=True)
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

    st.dataframe(trends, use_container_width=True)
    st.line_chart(trends.set_index("created_at")[["missed_count"]], height=180)
    st.line_chart(trends.set_index("created_at")[["avg_points_diff"]], height=180)
    st.line_chart(trends.set_index("created_at")[["profit_factor_diff"]], height=180)
    st.line_chart(trends.set_index("created_at")[["net_profit_delta"]], height=180)


def main() -> None:
    st.set_page_config(page_title="Post-Trade Comparison Dashboard", layout="wide")
    st.title("Post-Trade Comparison Dashboard")

    st.sidebar.header("Comparison Type")
    comparison_format = st.sidebar.selectbox(
        "Input format",
        options=[FORMAT_MT5_HTML_VS_MT5_HTML, FORMAT_NT_CSV_VS_MT5_XLSX],
        index=0,
        format_func=lambda k: FORMAT_LABELS.get(k, k),
        help="Choose the pair of input file formats for Source and Target.",
    )

    if comparison_format == FORMAT_MT5_HTML_VS_MT5_HTML:
        left_label = "Source"
        right_label = "Target"
        left_kind = "mt5_backtest_html"
        right_kind = "mt5_live_html"
        upload_left_label = "Source HTML (MT5 style)"
        upload_right_label = "Target HTML (MT5 style)"
        default_left_path = "data/raw/mt5_backtest/latest.html"
        default_right_path = "data/raw/mt5_live/latest.html"
        allowed_types = ["html", "htm"]
    else:
        left_label = "Source"
        right_label = "Target"
        left_kind = "nt_csv"
        right_kind = "mt5_xlsx"
        upload_left_label = "Source trades CSV"
        upload_right_label = "Target trades XLSX"
        default_left_path = "data/raw/ninjatrader/latest.csv"
        default_right_path = "data/raw/mt5/latest.xlsx"
        allowed_types = ["csv", "xlsx"]

    st.sidebar.header("Data Source")
    source_mode = st.sidebar.radio("Input mode", options=["Upload files", "Load from local paths"], index=0)

    left_file = None
    right_file = None
    left_filename = ""
    right_filename = ""

    if source_mode == "Upload files":
        if comparison_format == FORMAT_MT5_HTML_VS_MT5_HTML:
            mt5_files = st.sidebar.file_uploader(
                "Source/Target HTML uploads (MT5 style)",
                type=["html", "htm"],
                accept_multiple_files=True,
                help="Upload one or two files and assign one as Source and one as Target.",
            )
            if mt5_files:
                name_options = [f.name for f in mt5_files]
                file_by_name = {f.name: f for f in mt5_files}

                guessed_backtest = next((f.name for f in mt5_files if _guess_mt5_role(f.name) == "backtest"), None)
                guessed_live = next((f.name for f in mt5_files if _guess_mt5_role(f.name) == "live" and f.name != guessed_backtest), None)

                if guessed_backtest is None and len(name_options) >= 1:
                    guessed_backtest = name_options[0]
                if guessed_live is None and len(name_options) >= 2:
                    guessed_live = next((n for n in name_options if n != guessed_backtest), None)

                backtest_pick = st.sidebar.selectbox(
                    "Source file",
                    options=[""] + name_options,
                    index=([""] + name_options).index(guessed_backtest) if guessed_backtest else 0,
                )
                live_pick = st.sidebar.selectbox(
                    "Target file",
                    options=[""] + name_options,
                    index=([""] + name_options).index(guessed_live) if guessed_live else 0,
                )

                if backtest_pick:
                    left_file = file_by_name[backtest_pick]
                    left_filename = left_file.name
                if live_pick:
                    right_file = file_by_name[live_pick]
                    right_filename = right_file.name

                if backtest_pick and live_pick and backtest_pick == live_pick:
                    st.sidebar.warning("Source and Target must be different files.")
                    left_file = None
                    right_file = None
                    left_filename = ""
                    right_filename = ""
            else:
                st.sidebar.caption("Upload Source and Target HTML files to continue.")
        else:
            left_file = st.sidebar.file_uploader(upload_left_label, type=[allowed_types[0]])
            right_file = st.sidebar.file_uploader(upload_right_label, type=[allowed_types[1]])
            if left_file is not None:
                left_filename = left_file.name
            if right_file is not None:
                right_filename = right_file.name
    else:
        key_prefix = "mt5_html" if comparison_format == FORMAT_MT5_HTML_VS_MT5_HTML else "nt_mt5"
        left_path = st.sidebar.text_input(f"{left_label} path", value=default_left_path)
        right_path = st.sidebar.text_input(f"{right_label} path", value=default_right_path)
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

    st.sidebar.header("Settings")
    tz_base = ["UTC", "Etc/GMT-2", "Etc/GMT-3"]
    tz_choices = tz_base + sorted([tz for tz in available_timezones() if tz not in set(tz_base)])
    mt5_timezone = st.sidebar.selectbox("MT5 timezone", options=tz_choices, index=0)
    nt_entry_shift_seconds = 0
    quantity_mode = "ignore for matching"
    quantity_factor = 1.0
    if comparison_format == FORMAT_NT_CSV_VS_MT5_XLSX:
        st.sidebar.text_input("NinjaTrader timezone (fixed)", value=NT_TIMEZONE, disabled=True)
        nt_entry_shift_seconds = st.sidebar.number_input(
            "Source entry time shift (seconds)",
            min_value=-86400,
            max_value=86400,
            value=0,
            step=1,
        )
        quantity_mode = st.sidebar.selectbox(
            "Quantity matching mode",
            options=["ignore for matching", "convert using factor"],
            index=0,
        )
        if quantity_mode == "convert using factor":
            quantity_factor = st.sidebar.number_input("Quantity conversion factor", min_value=0.000001, value=1.0)

    symbol_map_text = st.sidebar.text_area("Symbol mapping (pattern=target, one per line)", value="MNQ*=NAS100", height=100)
    symbol_map = parse_symbol_map(symbol_map_text)

    st.sidebar.markdown("### Date Range Filter")

    available_min: pd.Timestamp | None = None
    available_max: pd.Timestamp | None = None
    left_preview = pd.DataFrame()
    right_preview = pd.DataFrame()
    if left_file is not None:
        try:
            left_preview = _load_normalized(left_file, left_kind, symbol_map, mt5_timezone)
            lmin, lmax = _entry_range(left_preview)
            available_min = lmin if available_min is None else min(available_min, lmin) if lmin is not None else available_min
            available_max = lmax if available_max is None else max(available_max, lmax) if lmax is not None else available_max
        except Exception:
            left_preview = pd.DataFrame()
    if right_file is not None:
        try:
            right_preview = _load_normalized(right_file, right_kind, symbol_map, mt5_timezone)
            rmin, rmax = _entry_range(right_preview)
            available_min = rmin if available_min is None else min(available_min, rmin) if rmin is not None else available_min
            available_max = rmax if available_max is None else max(available_max, rmax) if rmax is not None else available_max
        except Exception:
            right_preview = pd.DataFrame()

    today = pd.Timestamp.utcnow().tz_localize(None).date()
    default_start = (pd.Timestamp(today) - pd.Timedelta(days=13)).date()
    default_end = today

    # Prefer source range when available.
    backtest_min, backtest_max = _entry_range(left_preview)
    if backtest_min is not None and backtest_max is not None:
        default_start = backtest_min.date()
        default_end = backtest_max.date()

    filter_defaults_sig = f"{comparison_format}|{left_filename}|{right_filename}|{default_start}|{default_end}"
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
        elif date_filter_start > date_filter_end:
            st.error("Start date must be on or before end date.")
        else:
            date_start_ts = pd.Timestamp(date_filter_start)
            date_end_ts = pd.Timestamp(date_filter_end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            settings = {
                "algorithm": "Sequential",
                "comparison_kind": comparison_format,
                "comparison_kind_label": FORMAT_LABELS.get(comparison_format, comparison_format),
                "source_mode": source_mode,
                "mt5_timezone": mt5_timezone,
                "nt_entry_shift_seconds": int(nt_entry_shift_seconds),
                "quantity_mode": quantity_mode,
                "quantity_factor": float(quantity_factor),
                "symbol_map": symbol_map,
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
                quantity_mode=quantity_mode,
                quantity_factor=float(quantity_factor),
                nt_entry_shift_seconds=int(nt_entry_shift_seconds),
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

    installed_version = _read_installed_version()
    if installed_version:
        st.caption(f"PTA version: {installed_version}")
    else:
        st.caption("PTA version: development build")


if __name__ == "__main__":
    main()
