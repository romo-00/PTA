from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from .build_info import AGENT_BUILD_VERSION
from .db import get_connection, resolve_db_path, resolve_logs_dir

WATERMARK_KEY = "mt5_deals_watermark_utc"
LATE_ARRIVAL_BUFFER_MINUTES = 5
LOG_PATH = resolve_logs_dir() / "mt5_ingest.log"
DEFAULT_MT5_TERMINAL_PATH = r"C:\Program Files\Fusion Markets MT5 Terminal\terminal64.exe"


def _version_file_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    # Source layout: ...\src\pta_agent\mt5_ingest.py -> project root = parents[2]
    source_root = here.parents[2] if len(here.parents) > 2 else None
    frozen_base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    candidates: list[Path] = []
    if source_root is not None:
        candidates.append(source_root / "VERSION.txt")
    if frozen_base is not None:
        candidates.append(frozen_base / "VERSION.txt")
        candidates.append(frozen_base.parent / "VERSION.txt")
    return candidates


def _read_version_txt() -> str | None:
    for candidate in _version_file_candidates():
        try:
            if candidate.exists():
                text = candidate.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except Exception:
            continue
    return None


def _runtime_version() -> str:
    if "PTA_AGENT_VERSION" in os.environ and os.environ["PTA_AGENT_VERSION"].strip():
        return os.environ["PTA_AGENT_VERSION"].strip()
    file_version = _read_version_txt()
    if file_version:
        return file_version
    return AGENT_BUILD_VERSION


def _runtime_exe_path() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    return str(Path(__file__).resolve())


def configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def _import_mt5():
    try:
        import MetaTrader5 as mt5_mod

        return mt5_mod, None
    except Exception as exc:  # pragma: no cover - environment dependent
        return None, exc


def _deal_side(deal_type: int, mt5_mod) -> str:
    if deal_type == mt5_mod.DEAL_TYPE_BUY:
        return "BUY"
    if deal_type == mt5_mod.DEAL_TYPE_SELL:
        return "SELL"
    return "OTHER"


def _read_watermark_epoch(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute(
        "SELECT value FROM ingest_state WHERE key = ?",
        [WATERMARK_KEY],
    ).fetchone()
    if not row:
        return 0
    try:
        value = str(row[0]).strip()
        # Backward compatibility with older ISO watermark values.
        if value.endswith("Z") or "T" in value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
            return int(dt.timestamp())
        return max(0, int(value))
    except Exception:
        return 0


def _write_watermark_epoch(conn: duckdb.DuckDBPyConnection, watermark_epoch: int) -> None:
    value = str(max(0, int(watermark_epoch)))
    conn.execute(
        """
        INSERT INTO ingest_state(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [WATERMARK_KEY, value],
    )


def _deals_to_dataframe(deals: tuple, mt5_mod) -> pd.DataFrame:
    records: list[dict] = []
    for d in deals:
        time_utc = datetime.fromtimestamp(int(d.time), tz=timezone.utc).replace(tzinfo=None)
        records.append(
            {
                "deal_id": int(d.ticket),
                "time_utc": time_utc,
                "symbol": str(d.symbol or ""),
                "side": _deal_side(int(d.type), mt5_mod),
                "volume": float(d.volume),
                "price": float(d.price),
                "profit": float(d.profit),
                "commission": float(d.commission),
                "swap": float(d.swap),
                "comment": str(d.comment or ""),
                "magic": int(d.magic) if d.magic is not None else None,
                "position_id": int(d.position_id) if d.position_id is not None else None,
                "order_id": int(d.order) if d.order is not None else None,
            }
        )
    if not records:
        return pd.DataFrame(
            columns=[
                "deal_id",
                "time_utc",
                "symbol",
                "side",
                "volume",
                "price",
                "profit",
                "commission",
                "swap",
                "comment",
                "magic",
                "position_id",
                "order_id",
            ]
        )
    return pd.DataFrame(records)


def _upsert_deals(conn: duckdb.DuckDBPyConnection, deals_df: pd.DataFrame) -> None:
    if deals_df.empty:
        return
    conn.register("incoming_deals", deals_df)
    conn.execute(
        """
        INSERT INTO mt5_deals AS t
        SELECT * FROM incoming_deals
        ON CONFLICT(deal_id) DO UPDATE SET
            time_utc = excluded.time_utc,
            symbol = excluded.symbol,
            side = excluded.side,
            volume = excluded.volume,
            price = excluded.price,
            profit = excluded.profit,
            commission = excluded.commission,
            swap = excluded.swap,
            comment = excluded.comment,
            magic = excluded.magic,
            position_id = excluded.position_id,
            order_id = excluded.order_id
        """
    )


def _epoch(ts: datetime) -> int:
    return int(ts.timestamp())


def _from_epoch(epoch_seconds: int) -> datetime:
    return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc)


def _initialize_mt5_with_path(mt5_mod, mt5_terminal_path: str) -> bool:
    return bool(mt5_mod.initialize(path=mt5_terminal_path))


def _identity_lines(mt5_mod) -> tuple[str, str, str]:
    version = mt5_mod.version()
    term = mt5_mod.terminal_info()
    acct = mt5_mod.account_info()
    version_line = (
        f"mt5.version major={version[0] if version else None} "
        f"build={version[1] if version else None} release={version[2] if version else None}"
    )
    terminal_line = (
        "mt5.terminal_info "
        f"name={getattr(term, 'name', None)} "
        f"company={getattr(term, 'company', None)} "
        f"path={getattr(term, 'path', None)} "
        f"data_path={getattr(term, 'data_path', None)} "
        f"community_account={getattr(term, 'community_account', None)} "
        f"connected={getattr(term, 'connected', None)} "
        f"trade_allowed={getattr(term, 'trade_allowed', None)}"
    )
    account_line = (
        "mt5.account_info "
        f"login={getattr(acct, 'login', None)} "
        f"server={getattr(acct, 'server', None)} "
        f"currency={getattr(acct, 'currency', None)} "
        f"leverage={getattr(acct, 'leverage', None)}"
    )
    return version_line, terminal_line, account_line


def _log_identity(mt5_mod) -> None:
    version_line, terminal_line, account_line = _identity_lines(mt5_mod)
    logging.info(version_line)
    logging.info(terminal_line)
    logging.info(account_line)


def _print_and_log_identity(mt5_mod) -> None:
    version_line, terminal_line, account_line = _identity_lines(mt5_mod)
    logging.info(version_line)
    logging.info(terminal_line)
    logging.info(account_line)
    print(version_line)
    print(terminal_line)
    print(account_line)


def _print_debug_deals(deals) -> None:
    if not deals:
        line = "history_deals_get(0, now_utc): total=0"
        logging.info(line)
        print(line)
        return

    sorted_deals = sorted(deals, key=lambda d: int(getattr(d, "time", 0) or 0))
    min_epoch = int(sorted_deals[0].time)
    max_epoch = int(sorted_deals[-1].time)
    summary = (
        "history_deals_get(0, now_utc): "
        f"total={len(sorted_deals)} min_time_utc={_from_epoch(min_epoch).isoformat()} "
        f"max_time_utc={_from_epoch(max_epoch).isoformat()}"
    )
    logging.info(summary)
    print(summary)

    newest = sorted(sorted_deals, key=lambda d: int(getattr(d, "time", 0) or 0), reverse=True)[:5]
    for d in newest:
        line = (
            "deal_sample "
            f"deal_id={int(getattr(d, 'ticket', 0) or 0)} "
            f"time_utc={_from_epoch(int(getattr(d, 'time', 0) or 0)).isoformat()} "
            f"symbol={str(getattr(d, 'symbol', '') or '')}"
        )
        logging.info(line)
        print(line)


def run_debug_mt5(mt5_terminal_path: str | None = None) -> int:
    configure_logging()
    terminal_path = mt5_terminal_path or DEFAULT_MT5_TERMINAL_PATH
    logging.info(
        "PTA MT5 debug-mt5 starting (version=%s, exe=%s, pid=%s, terminal_path=%s)",
        _runtime_version(),
        _runtime_exe_path(),
        os.getpid(),
        terminal_path,
    )

    mt5_mod, mt5_import_err = _import_mt5()
    if mt5_mod is None:
        message = f"MT5 import failed: {mt5_import_err}"
        logging.error(message)
        print(message)
        return 1

    if not _initialize_mt5_with_path(mt5_mod, terminal_path):
        err = mt5_mod.last_error()
        message = f"MT5 initialize failed. terminal_path={terminal_path} last_error={err}"
        logging.error(message)
        print(message)
        return 2

    logging.info("MT5 initialize(path=...) succeeded. last_error=%s", mt5_mod.last_error())
    _print_and_log_identity(mt5_mod)

    try:
        now_utc = datetime.now(timezone.utc)
        deals = mt5_mod.history_deals_get(0, now_utc)
        if deals is None:
            err = mt5_mod.last_error()
            line = f"history_deals_get(0, now_utc) returned None last_error={err}"
            logging.error(line)
            print(line)
            return 0

        _print_debug_deals(deals)
        return 0
    finally:
        mt5_mod.shutdown()


def run_ingest(
    db_path: str | None = None,
    lookback_minutes: int | None = None,
    reset_watermark: bool = False,
    mt5_terminal_path: str | None = None,
) -> int:
    configure_logging()
    db_target = resolve_db_path(db_path)
    terminal_path = mt5_terminal_path or DEFAULT_MT5_TERMINAL_PATH
    logging.info(
        "PTA MT5 ingest starting (version=%s, exe=%s, pid=%s, terminal_path=%s)",
        _runtime_version(),
        _runtime_exe_path(),
        os.getpid(),
        terminal_path,
    )

    mt5_mod, mt5_import_err = _import_mt5()
    if mt5_mod is None:
        message = f"MT5 import failed: {mt5_import_err}"
        logging.error(message)
        print(message)
        return 1

    if not _initialize_mt5_with_path(mt5_mod, terminal_path):
        err = mt5_mod.last_error()
        message = f"MT5 initialize failed. terminal_path={terminal_path} last_error={err}"
        logging.error(message)
        print(message)
        return 2
    logging.info("MT5 initialize(path=...) succeeded. last_error=%s", mt5_mod.last_error())
    _log_identity(mt5_mod)

    try:
        conn = get_connection(str(db_target))
        try:
            now_utc = datetime.now(timezone.utc)
            now_epoch = _epoch(now_utc)
            if reset_watermark:
                _write_watermark_epoch(conn, 0)
                logging.info("Reset watermark requested. Set ingest_state key '%s' to 0.", WATERMARK_KEY)
            watermark_epoch = _read_watermark_epoch(conn)

            effective_lookback = int(lookback_minutes) if lookback_minutes is not None else LATE_ARRIVAL_BUFFER_MINUTES
            from_epoch = now_epoch - (effective_lookback * 60)
            to_epoch = now_epoch
            from_utc = _from_epoch(from_epoch)
            to_utc = _from_epoch(to_epoch)
            logging.info(
                "MT5 query window utc: from=%s to=%s now=%s | epoch: from=%s to=%s now=%s watermark_epoch=%s",
                from_utc.isoformat(),
                to_utc.isoformat(),
                now_utc.isoformat(),
                from_epoch,
                to_epoch,
                now_epoch,
                watermark_epoch,
            )

            deals = mt5_mod.history_deals_get(0, to_utc)
            if deals is None:
                err = mt5_mod.last_error()
                logging.error("history_deals_get(0,to_utc) returned None (to=%s) last_error=%s", to_utc.isoformat(), err)
                print(f"MT5 history_deals_get failed. last_error={err}")
                return 3
            logging.info("history_deals_get(0,to_utc) returned %s deals (to=%s)", len(deals), to_utc.isoformat())
            if len(deals) > 0:
                deal_times = [datetime.fromtimestamp(int(d.time), tz=timezone.utc) for d in deals]
                sample_ids = [int(d.ticket) for d in deals[:3]]
                logging.info(
                    "history_deals_get(0,to_utc) time range: min=%s max=%s sample_deal_ids=%s",
                    min(deal_times).isoformat(),
                    max(deal_times).isoformat(),
                    sample_ids,
                )
                newest = sorted(deals, key=lambda d: int(getattr(d, "time", 0) or 0), reverse=True)[:5]
                for d in newest:
                    logging.info(
                        "deal_newest deal_id=%s time_utc=%s symbol=%s",
                        int(getattr(d, "ticket", 0) or 0),
                        _from_epoch(int(getattr(d, "time", 0) or 0)).isoformat(),
                        str(getattr(d, "symbol", "") or ""),
                    )

            filtered_deals = []
            for d in deals:
                deal_epoch = int(getattr(d, "time", 0) or 0)
                if deal_epoch < from_epoch or deal_epoch > to_epoch:
                    continue
                if deal_epoch <= watermark_epoch:
                    continue
                symbol = str(getattr(d, "symbol", "") or "").strip()
                if not symbol:
                    continue
                if _deal_side(int(getattr(d, "type", -1)), mt5_mod) == "OTHER":
                    continue
                filtered_deals.append(d)

            logging.info("Filtered deals in window and >watermark: %s", len(filtered_deals))
            if filtered_deals:
                filtered_epochs = [int(d.time) for d in filtered_deals]
                logging.info(
                    "Filtered deal.time range: min=%s (%s) max=%s (%s)",
                    min(filtered_epochs),
                    _from_epoch(min(filtered_epochs)).isoformat(),
                    max(filtered_epochs),
                    _from_epoch(max(filtered_epochs)).isoformat(),
                )
            else:
                logging.info(
                    "No new deals after filtering (window=%s..%s, watermark_epoch=%s). Watermark unchanged.",
                    from_utc.isoformat(),
                    to_utc.isoformat(),
                    watermark_epoch,
                )

            deals_df = _deals_to_dataframe(tuple(filtered_deals), mt5_mod)
            _upsert_deals(conn, deals_df)
            if not deals_df.empty:
                new_watermark_epoch = max(int(d.time) for d in filtered_deals)
                _write_watermark_epoch(conn, new_watermark_epoch)
                logging.info(
                    "Ingested %s deals (%s..%s). WatermarkEpoch=%s (%s)",
                    len(deals_df),
                    from_utc.isoformat(),
                    to_utc.isoformat(),
                    new_watermark_epoch,
                    _from_epoch(new_watermark_epoch).isoformat(),
                )

            return 0
        finally:
            conn.close()
    finally:
        mt5_mod.shutdown()


def run_status(db_path: str | None = None, mt5_terminal_path: str | None = None) -> int:
    configure_logging()
    db_target = resolve_db_path(db_path)
    log_target = LOG_PATH
    terminal_path = mt5_terminal_path or DEFAULT_MT5_TERMINAL_PATH
    logging.info(
        "PTA MT5 status starting (version=%s, exe=%s, pid=%s, terminal_path=%s)",
        _runtime_version(),
        _runtime_exe_path(),
        os.getpid(),
        terminal_path,
    )

    mt5_mod, mt5_import_err = _import_mt5()
    mt5_ok = False
    mt5_err = None
    if mt5_mod is None:
        mt5_err = f"import_failed: {mt5_import_err}"
    else:
        mt5_ok = _initialize_mt5_with_path(mt5_mod, terminal_path)
        mt5_err = mt5_mod.last_error()
        if mt5_ok:
            mt5_mod.shutdown()

    print(f"PTA Agent version: {_runtime_version()}")
    print(f"PTA Agent exe: {_runtime_exe_path()}")
    print(f"MT5 terminal path: {terminal_path}")
    print(f"MT5 initialize: {'OK' if mt5_ok else 'FAIL'}")
    print(f"MT5 last_error: {mt5_err}")
    print(f"DB path: {db_target}")
    print(f"Log path: {log_target}")

    deals_count = 0
    watermark = None
    if db_target.exists():
        conn = get_connection(str(db_target))
        try:
            deals_count = int(conn.execute("SELECT COUNT(*) FROM mt5_deals").fetchone()[0])
            row = conn.execute("SELECT value FROM ingest_state WHERE key = ?", [WATERMARK_KEY]).fetchone()
            watermark = row[0] if row else None
        finally:
            conn.close()

    print(f"mt5_deals count: {deals_count}")
    print(f"watermark_epoch: {watermark or '0'}")
    logging.info(
        "PTA MT5 status complete (version=%s, mt5_ok=%s, db=%s, deals=%s, watermark_epoch=%s)",
        _runtime_version(),
        mt5_ok,
        db_target,
        deals_count,
        watermark or "0",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PTA Agent MT5 ingestion")
    parser.add_argument("--db", default=str(resolve_db_path()), help="DuckDB file path")
    parser.add_argument("--lookback-minutes", type=int, default=None, help="Override lookback window in minutes")
    parser.add_argument("--reset-watermark", action="store_true", help="Clear persisted MT5 watermark before ingest")
    parser.add_argument(
        "--mt5-terminal-path",
        default=DEFAULT_MT5_TERMINAL_PATH,
        help="Path to terminal64.exe to bind MetaTrader5 API",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_ingest(
        args.db,
        lookback_minutes=args.lookback_minutes,
        reset_watermark=args.reset_watermark,
        mt5_terminal_path=args.mt5_terminal_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
