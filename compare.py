from __future__ import annotations

import argparse
from pathlib import Path

from importers.mt5_xlsx import load_mt5_xlsx
from importers.ninjatrader_csv import load_ninjatrader_csv
from trade_matching import MatchConfig, match_trades

DEFAULT_SYMBOL_MAP = {"MNQ*": "NAS100"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare NinjaTrader backtest trades vs MT5 actual trades.")
    parser.add_argument("--nt", required=True, help="Path to NinjaTrader CSV")
    parser.add_argument("--mt5", required=True, help="Path to MT5 XLSX")
    parser.add_argument("--mt5-timezone", default="UTC", help="IANA timezone for MT5 times")
    parser.add_argument("--nt-entry-shift-seconds", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    nt_df = load_ninjatrader_csv(args.nt, DEFAULT_SYMBOL_MAP)
    mt5_df = load_mt5_xlsx(args.mt5, DEFAULT_SYMBOL_MAP, mt5_timezone=args.mt5_timezone)

    matched, unmatched_nt, unmatched_mt5 = match_trades(
        nt_df,
        mt5_df,
        MatchConfig(nt_entry_shift_seconds=args.nt_entry_shift_seconds),
    )

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    matched.to_csv(reports_dir / "matched.csv", index=False)
    unmatched_nt.to_csv(reports_dir / "unmatched_nt.csv", index=False)
    unmatched_mt5.to_csv(reports_dir / "unmatched_mt5.csv", index=False)

    print(f"NinjaTrader trades: {len(nt_df)}")
    print(f"MT5 trades: {len(mt5_df)}")
    print(f"Matched: {len(matched)}")
    print(f"Unmatched NT: {len(unmatched_nt)}")
    print(f"Unmatched MT5: {len(unmatched_mt5)}")


if __name__ == "__main__":
    main()
