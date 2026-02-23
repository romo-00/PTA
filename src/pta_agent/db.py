from __future__ import annotations

import os
from pathlib import Path

import duckdb


def resolve_commonappdata_pta_dir() -> Path:
    programdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return Path(programdata) / "PTA"


def resolve_logs_dir() -> Path:
    return resolve_commonappdata_pta_dir() / "logs"


DEFAULT_DB_PATH = resolve_commonappdata_pta_dir() / "pta.duckdb"


def resolve_db_path(db_path: str | None = None) -> Path:
    if db_path:
        return Path(db_path)
    return DEFAULT_DB_PATH


def ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mt5_deals (
            deal_id BIGINT PRIMARY KEY,
            time_utc TIMESTAMP,
            symbol TEXT,
            side TEXT,
            volume DOUBLE,
            price DOUBLE,
            profit DOUBLE,
            commission DOUBLE,
            swap DOUBLE,
            comment TEXT,
            magic BIGINT,
            position_id BIGINT,
            order_id BIGINT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )


def get_connection(db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    target = resolve_db_path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(target))
    ensure_schema(conn)
    return conn
