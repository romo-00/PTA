from __future__ import annotations

import pandas as pd

from normalization import coerce_normalized, ensure_columns, normalize_side, normalize_symbol, to_utc_naive

MT5_REQUIRED = [
    "Type",
    "Ticket",
    "Symbol",
    "Lots",
    "Buy/sell",
    "Open price",
    "Close price",
    "Open time",
    "Close time",
    "Open date",
    "Close date",
    "Profit",
    "Swap",
    "Commission",
    "Net profit",
    "T/P",
    "S/L",
    "Pips",
    "Result",
    "Trade duration (hours)",
    "Magic number",
    "Order comment",
    "Account",
]


def _build_dt(date_col: pd.Series | None, time_col: pd.Series | None, fallback_col: pd.Series | None) -> pd.Series:
    combined = pd.Series(pd.NaT, index=(fallback_col.index if fallback_col is not None else date_col.index))

    if date_col is not None and time_col is not None:
        date_part = pd.to_datetime(date_col, errors="coerce").dt.strftime("%Y-%m-%d")
        time_part = pd.to_datetime(time_col, errors="coerce").dt.strftime("%H:%M:%S")
        combined_str = date_part + " " + time_part
        combined = pd.to_datetime(combined_str, errors="coerce")

    if fallback_col is not None:
        fallback = pd.to_datetime(fallback_col, errors="coerce")
        combined = combined.where(combined.notna(), fallback)

    return combined


def load_mt5_xlsx(file_obj, symbol_map: dict[str, str], mt5_timezone: str = "UTC") -> pd.DataFrame:
    df = pd.read_excel(file_obj)
    ensure_columns(df, MT5_REQUIRED, "MT5")

    open_dt = _build_dt(df.get("Open date"), df.get("Open time"), df.get("Open time"))
    close_dt = _build_dt(df.get("Close date"), df.get("Close time"), df.get("Close time"))

    net_profit_col = "Net profit" if "Net profit" in df.columns else "Profit"

    out = pd.DataFrame(
        {
            "source": "mt5",
            "trade_id": df["Ticket"],
            "symbol_raw": df["Symbol"],
            "symbol_norm": df["Symbol"].map(lambda s: normalize_symbol(str(s), symbol_map)),
            "side": df["Buy/sell"].map(lambda s: normalize_side(str(s))),
            "qty": df["Lots"],
            "entry_time_utc": to_utc_naive(open_dt, mt5_timezone),
            "exit_time_utc": to_utc_naive(close_dt, mt5_timezone),
            "entry_price": df["Open price"],
            "exit_price": df["Close price"],
            "net_profit": df[net_profit_col],
        }
    )
    return coerce_normalized(out)
