from __future__ import annotations

import pandas as pd

from normalization import coerce_normalized, ensure_columns, normalize_side, normalize_symbol, to_utc_naive

NT_TIMEZONE = "America/New_York"

NT_REQUIRED = [
    "Trade number",
    "Instrument",
    "Market pos.",
    "Qty",
    "Entry price",
    "Exit price",
    "Entry time",
    "Exit time",
    "Profit",
]


def load_ninjatrader_csv(file_obj, symbol_map: dict[str, str]) -> pd.DataFrame:
    df = pd.read_csv(file_obj)
    ensure_columns(df, NT_REQUIRED, "NinjaTrader")

    entry_time_utc = to_utc_naive(df["Entry time"], NT_TIMEZONE)
    exit_time_utc = to_utc_naive(df["Exit time"], NT_TIMEZONE)

    out = pd.DataFrame(
        {
            "source": "ninjatrader",
            "trade_id": df["Trade number"],
            "symbol_raw": df["Instrument"],
            "symbol_norm": df["Instrument"].map(lambda s: normalize_symbol(str(s), symbol_map)),
            "side": df["Market pos."].map(lambda s: normalize_side(str(s))),
            "qty": df["Qty"],
            "entry_time_utc": entry_time_utc,
            "exit_time_utc": exit_time_utc,
            "entry_price": df["Entry price"],
            "exit_price": df["Exit price"],
            "net_profit": df["Profit"],
        }
    )
    return coerce_normalized(out)
