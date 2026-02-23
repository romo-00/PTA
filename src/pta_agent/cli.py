from __future__ import annotations

import argparse

from .db import resolve_db_path
from .mt5_ingest import DEFAULT_MT5_TERMINAL_PATH, run_debug_mt5, run_ingest, run_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="PTAAgent", description="PTA internal agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest MT5 deals into DuckDB")
    ingest.add_argument("--db", default=str(resolve_db_path()), help="DuckDB file path")
    ingest.add_argument("--lookback-minutes", type=int, default=None, help="Override lookback window in minutes")
    ingest.add_argument("--reset-watermark", action="store_true", help="Clear persisted MT5 watermark before ingest")
    ingest.add_argument(
        "--mt5-terminal-path",
        default=DEFAULT_MT5_TERMINAL_PATH,
        help="Path to terminal64.exe to bind MetaTrader5 API",
    )

    status = sub.add_parser("status", help="Print MT5 + DB + watermark status")
    status.add_argument("--db", default=str(resolve_db_path()), help="DuckDB file path")
    status.add_argument(
        "--mt5-terminal-path",
        default=DEFAULT_MT5_TERMINAL_PATH,
        help="Path to terminal64.exe to bind MetaTrader5 API",
    )

    debug_mt5 = sub.add_parser("debug-mt5", help="Debug MT5 terminal identity and deal visibility")
    debug_mt5.add_argument(
        "--mt5-terminal-path",
        default=DEFAULT_MT5_TERMINAL_PATH,
        help="Path to terminal64.exe to bind MetaTrader5 API",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "ingest":
        return run_ingest(
            args.db,
            lookback_minutes=args.lookback_minutes,
            reset_watermark=args.reset_watermark,
            mt5_terminal_path=args.mt5_terminal_path,
        )

    if args.command == "status":
        return run_status(args.db, mt5_terminal_path=args.mt5_terminal_path)

    if args.command == "debug-mt5":
        return run_debug_mt5(mt5_terminal_path=args.mt5_terminal_path)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
