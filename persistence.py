from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

DB_PATH = Path(os.getenv("PTA_DB_PATH", "data/pta.duckdb"))
DEFAULT_READ_ONLY = os.getenv("PTA_DB_READ_ONLY", "0").strip().lower() in {"1", "true", "yes"}


def get_connection(read_only: bool | None = None) -> duckdb.DuckDBPyConnection:
    if read_only is None:
        read_only = DEFAULT_READ_ONLY

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if read_only and not DB_PATH.exists():
        # Dashboard may run before agent creates the DB; return empty in-memory schema.
        conn = duckdb.connect(":memory:")
        _init_schema(conn)
        return conn

    conn = duckdb.connect(str(DB_PATH), read_only=read_only)
    if not read_only:
        _init_schema(conn)
    return conn


def _init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id VARCHAR PRIMARY KEY,
            created_at TIMESTAMP,
            nt_filename VARCHAR,
            mt5_filename VARCHAR,
            settings_json VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nt_trades (
            run_id VARCHAR,
            source VARCHAR,
            trade_id VARCHAR,
            symbol_raw VARCHAR,
            symbol_norm VARCHAR,
            side VARCHAR,
            qty DOUBLE,
            entry_time_utc TIMESTAMP,
            exit_time_utc TIMESTAMP,
            entry_price DOUBLE,
            exit_price DOUBLE,
            net_profit DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mt5_trades (
            run_id VARCHAR,
            source VARCHAR,
            trade_id VARCHAR,
            symbol_raw VARCHAR,
            symbol_norm VARCHAR,
            side VARCHAR,
            qty DOUBLE,
            entry_time_utc TIMESTAMP,
            exit_time_utc TIMESTAMP,
            entry_price DOUBLE,
            exit_price DOUBLE,
            net_profit DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS matches (
            run_id VARCHAR,
            nt_trade_id VARCHAR,
            mt5_trade_id VARCHAR,
            match_mode VARCHAR,
            confidence VARCHAR,
            entry_delta_sec DOUBLE,
            exit_delta_sec DOUBLE,
            entry_slippage DOUBLE,
            exit_slippage DOUBLE,
            qty_delta DOUBLE,
            net_profit_delta DOUBLE,
            notes VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS unmatched (
            run_id VARCHAR,
            source VARCHAR,
            trade_id VARCHAR,
            reason VARCHAR
        )
        """
    )


def _prepare_trade_df(df: pd.DataFrame, run_id: str) -> pd.DataFrame:
    out = df.copy()
    out["run_id"] = run_id
    for col in ["source", "trade_id", "symbol_raw", "symbol_norm", "side", "qty", "entry_time_utc", "exit_time_utc", "entry_price", "exit_price", "net_profit"]:
        if col not in out.columns:
            out[col] = pd.NA
    return out[["run_id", "source", "trade_id", "symbol_raw", "symbol_norm", "side", "qty", "entry_time_utc", "exit_time_utc", "entry_price", "exit_price", "net_profit"]]


def _prepare_matches_df(df: pd.DataFrame, run_id: str) -> pd.DataFrame:
    out = df.copy()
    out["run_id"] = run_id

    if "entry_slippage" not in out.columns and "model_to_live_entry_difference_pts" in out.columns:
        out["entry_slippage"] = out["model_to_live_entry_difference_pts"]
    if "exit_slippage" not in out.columns and "model_to_live_exit_difference_pts" in out.columns:
        out["exit_slippage"] = out["model_to_live_exit_difference_pts"]

    if "match_mode" not in out.columns and "match_type" in out.columns:
        out["match_mode"] = out["match_type"]

    required = [
        "run_id",
        "nt_trade_id",
        "mt5_trade_id",
        "match_mode",
        "confidence",
        "entry_delta_sec",
        "exit_delta_sec",
        "entry_slippage",
        "exit_slippage",
        "qty_delta",
        "net_profit_delta",
        "notes",
    ]
    for col in required:
        if col not in out.columns:
            out[col] = pd.NA
    return out[required]


def _prepare_unmatched_df(nt_unmatched: pd.DataFrame, mt5_unmatched: pd.DataFrame, run_id: str) -> pd.DataFrame:
    nt = nt_unmatched.copy()
    mt = mt5_unmatched.copy()
    nt["source"] = "NT"
    mt["source"] = "MT5"
    combined = pd.concat([nt, mt], ignore_index=True)
    combined["run_id"] = run_id
    if "reason" not in combined.columns:
        combined["reason"] = "NO_CANDIDATE"
    if "trade_id" not in combined.columns:
        combined["trade_id"] = pd.NA
    return combined[["run_id", "source", "trade_id", "reason"]]


def persist_run(
    run_id: str,
    created_at: pd.Timestamp,
    nt_filename: str,
    mt5_filename: str,
    settings: dict[str, Any],
    nt_df: pd.DataFrame,
    mt5_df: pd.DataFrame,
    matches_df: pd.DataFrame,
    unmatched_nt_df: pd.DataFrame,
    unmatched_mt5_df: pd.DataFrame,
) -> None:
    conn = get_connection(read_only=False)
    conn.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?)", [run_id, created_at.to_pydatetime(), nt_filename, mt5_filename, json.dumps(settings)])

    conn.register("nt_insert", _prepare_trade_df(nt_df, run_id))
    conn.register("mt5_insert", _prepare_trade_df(mt5_df, run_id))
    conn.register("matches_insert", _prepare_matches_df(matches_df, run_id))
    conn.register("unmatched_insert", _prepare_unmatched_df(unmatched_nt_df, unmatched_mt5_df, run_id))

    conn.execute("INSERT INTO nt_trades SELECT * FROM nt_insert")
    conn.execute("INSERT INTO mt5_trades SELECT * FROM mt5_insert")
    conn.execute("INSERT INTO matches SELECT * FROM matches_insert")
    conn.execute("INSERT INTO unmatched SELECT * FROM unmatched_insert")
    conn.close()


def load_run(run_id: str) -> dict[str, pd.DataFrame]:
    conn = get_connection()
    try:
        run_row = conn.execute("SELECT settings_json FROM runs WHERE run_id = ?", [run_id]).fetchone()
        settings = json.loads(run_row[0]) if run_row and run_row[0] else {}
        nt_norm = conn.execute("SELECT * EXCLUDE(run_id) FROM nt_trades WHERE run_id = ?", [run_id]).fetchdf()
        mt5_norm = conn.execute("SELECT * EXCLUDE(run_id) FROM mt5_trades WHERE run_id = ?", [run_id]).fetchdf()

        matched = conn.execute(
            """
            SELECT
                nt.trade_id AS nt_trade_id,
                mt.trade_id AS mt5_trade_id,
                nt.symbol_norm,
                nt.side,
                nt.entry_time_utc AS nt_entry_time_utc,
                mt.entry_time_utc AS mt5_entry_time_utc,
                nt.exit_time_utc AS nt_exit_time_utc,
                mt.exit_time_utc AS mt5_exit_time_utc,
                nt.entry_price AS nt_entry_price,
                mt.entry_price AS mt5_entry_price,
                nt.exit_price AS nt_exit_price,
                mt.exit_price AS mt5_exit_price,
                nt.qty AS nt_qty,
                mt.qty AS mt5_qty,
                m.entry_slippage AS model_to_live_entry_difference_pts,
                m.exit_slippage AS model_to_live_exit_difference_pts,
                m.qty_delta,
                m.net_profit_delta,
                m.match_mode AS match_type,
                m.notes
            FROM matches m
            LEFT JOIN nt_trades nt ON nt.run_id = m.run_id AND nt.trade_id = m.nt_trade_id
            LEFT JOIN mt5_trades mt ON mt.run_id = m.run_id AND mt.trade_id = m.mt5_trade_id
            WHERE m.run_id = ?
            """,
            [run_id],
        ).fetchdf()

        unmatched_raw = conn.execute("SELECT source, trade_id, reason FROM unmatched WHERE run_id = ?", [run_id]).fetchdf()
    except duckdb.Error:
        settings = {}
        nt_norm = pd.DataFrame()
        mt5_norm = pd.DataFrame()
        matched = pd.DataFrame()
        unmatched_raw = pd.DataFrame(columns=["source", "trade_id", "reason"])
    finally:
        conn.close()

    return {
        "nt_norm": nt_norm,
        "mt5_norm": mt5_norm,
        "matched": matched,
        "unmatched_nt": unmatched_raw[unmatched_raw["source"] == "NT"].copy() if not unmatched_raw.empty else pd.DataFrame(),
        "unmatched_mt5": unmatched_raw[unmatched_raw["source"] == "MT5"].copy() if not unmatched_raw.empty else pd.DataFrame(),
        "settings": settings,
    }


def _points(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    side = df["side"].astype(str).str.upper()
    entry = pd.to_numeric(df["entry_price"], errors="coerce")
    exit_ = pd.to_numeric(df["exit_price"], errors="coerce")
    return pd.Series(np.where(side == "BUY", exit_ - entry, np.where(side == "SELL", entry - exit_, np.nan)), index=df.index)


def _profit_factor(points: pd.Series) -> float:
    if points.empty:
        return 0.0
    wins = points[points > 0].sum()
    losses = points[points < 0].sum()
    if losses == 0:
        return 999999.0 if wins > 0 else 0.0
    return float(wins / abs(losses))


def run_history() -> pd.DataFrame:
    conn = get_connection()
    try:
        runs = conn.execute("SELECT run_id, created_at, nt_filename, mt5_filename, settings_json FROM runs ORDER BY created_at DESC").fetchdf()
    except duckdb.Error:
        conn.close()
        return pd.DataFrame()

    rows: list[dict] = []
    for _, r in runs.iterrows():
        run_id = r["run_id"]
        try:
            settings = json.loads(r["settings_json"]) if pd.notna(r.get("settings_json")) and r.get("settings_json") else {}
        except Exception:
            settings = {}
        nt = conn.execute("SELECT side, entry_price, exit_price FROM nt_trades WHERE run_id = ?", [run_id]).fetchdf()
        mt = conn.execute("SELECT side, entry_price, exit_price FROM mt5_trades WHERE run_id = ?", [run_id]).fetchdf()
        matched = conn.execute("SELECT net_profit_delta FROM matches WHERE run_id = ?", [run_id]).fetchdf()
        missed = conn.execute("SELECT COUNT(*) AS c FROM unmatched WHERE run_id = ?", [run_id]).fetchdf().iloc[0]["c"]

        nt_pts = _points(nt)
        mt_pts = _points(mt)

        avg_points_diff = float(mt_pts.mean() - nt_pts.mean()) if (not nt_pts.empty and not mt_pts.empty) else 0.0
        pf_diff = (_profit_factor(mt_pts) - _profit_factor(nt_pts)) if (not nt_pts.empty and not mt_pts.empty) else 0.0
        net_delta = float(pd.to_numeric(matched.get("net_profit_delta"), errors="coerce").fillna(0).sum()) if not matched.empty else 0.0

        rows.append(
            {
                "run_id": run_id,
                "created_at": r["created_at"],
                "nt_filename": r["nt_filename"],
                "mt5_filename": r["mt5_filename"],
                "date_filter_start": settings.get("date_filter_start"),
                "date_filter_end": settings.get("date_filter_end"),
                "missed_count": int(missed),
                "avg_points_diff": avg_points_diff,
                "profit_factor_diff": pf_diff,
                "net_profit_delta": net_delta,
            }
        )

    conn.close()
    return pd.DataFrame(rows)


def trend_history(limit_runs: int) -> pd.DataFrame:
    history = run_history()
    if history.empty:
        return history
    return history.head(limit_runs).sort_values("created_at")

