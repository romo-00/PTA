from __future__ import annotations

import pandas as pd

from importers.mt5_html import _normalize_deals_rows, _reconstruct_closed_trades
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

MT5_DEALS_REQUIRED = [
    "Time",
    "Deal",
    "Symbol",
    "Type",
    "Direction",
    "Volume",
    "Price",
]

MT5_MINIMAL_DEALS_REQUIRED = [
    "Time",
    "Deal",
    "Type",
    "Direction",
    "Volume",
    "Price",
    "Profit",
    "Balance",
]

MT5_REPORT_POSITIONS_REQUIRED = [
    "Time",
    "Position",
    "Symbol",
    "Type",
    "Volume",
    "Price",
    "Time.1",
    "Price.1",
    "Commission",
    "Swap",
    "Profit",
]

CTRADER_CLOSED_TRADES_REQUIRED = [
    "Symbol",
    "Opening direction",
    "Opening time",
    "Closing time",
    "Entry price",
    "Closing price",
    "Closing Quantity",
    "Net $",
]


def _find_report_header_row(file_obj, max_rows: int = 15) -> int | None:
    df = pd.read_excel(file_obj, header=None, nrows=max_rows)
    for idx in df.index:
        row_values = {str(value).strip() for value in df.loc[idx].tolist() if pd.notna(value)}
        if {"Time", "Position", "Symbol", "Type", "Volume", "Price"}.issubset(row_values):
            return int(idx)
    return None


def peek_mt5_xlsx_columns(file_obj) -> list[str]:
    df = pd.read_excel(file_obj, nrows=0)
    cols = [str(col) for col in df.columns]
    if all(col in cols for col in MT5_REQUIRED) or all(col in cols for col in MT5_DEALS_REQUIRED) or all(
        col in cols for col in MT5_MINIMAL_DEALS_REQUIRED
    ) or all(col in cols for col in CTRADER_CLOSED_TRADES_REQUIRED):
        return cols

    header_row = _find_report_header_row(file_obj)
    if header_row is not None:
        report_df = pd.read_excel(file_obj, header=header_row, nrows=0)
        return [str(col) for col in report_df.columns]
    return cols


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


def _load_mt5_closed_trades_xlsx(df: pd.DataFrame, symbol_map: dict[str, str], mt5_timezone: str) -> pd.DataFrame:
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


def _load_mt5_deals_xlsx(df: pd.DataFrame, symbol_map: dict[str, str], mt5_timezone: str) -> pd.DataFrame:
    ensure_columns(df, MT5_DEALS_REQUIRED, "MT5 deals")
    deals = _normalize_deals_rows(df, "mt5_live_deal")
    return _reconstruct_closed_trades(deals, symbol_map, mt5_timezone, "mt5_live")


def _load_mt5_minimal_deals_xlsx(df: pd.DataFrame, symbol_map: dict[str, str], mt5_timezone: str) -> pd.DataFrame:
    ensure_columns(df, MT5_MINIMAL_DEALS_REQUIRED, "MT5 minimal deals")

    symbol_raw = pd.Series(["UNKNOWN"] * len(df), index=df.index)
    deals = pd.DataFrame(
        {
            "time": pd.to_datetime(df["Time"], errors="coerce"),
            "symbol": symbol_raw,
            "side_type": df["Type"].astype(str).str.strip(),
            "direction": df["Direction"].astype(str).str.strip().str.lower(),
            "volume": pd.to_numeric(df["Volume"], errors="coerce"),
            "price": pd.to_numeric(df["Price"], errors="coerce"),
            "order_id": pd.Series(pd.array([pd.NA] * len(df), dtype="Int64")),
            "deal_id": pd.to_numeric(df["Deal"], errors="coerce").round().astype("Int64"),
            "comment": "",
            "profit": pd.to_numeric(df.get("Profit"), errors="coerce").fillna(0.0),
            "commission": 0.0,
            "swap": 0.0,
            "source_deal": "mt5_live_deal",
        }
    )
    deals["side"] = deals["side_type"].map(lambda s: normalize_side(str(s)))
    deals = deals[deals["time"].notna() & deals["price"].notna()].copy()
    deals = deals[deals["side_type"].str.lower() != "balance"].copy()
    deals = deals[deals["side"].isin(["BUY", "SELL"])].copy()
    deals = deals[deals["volume"].notna() & (deals["volume"] > 0)].copy()
    if deals.empty:
        raise ValueError("MT5 minimal deals XLSX did not contain any usable in/out trade rows.")
    return _reconstruct_closed_trades(deals, symbol_map, mt5_timezone, "mt5_live")


def _load_mt5_positions_report_xlsx(df: pd.DataFrame, symbol_map: dict[str, str], mt5_timezone: str) -> pd.DataFrame:
    ensure_columns(df, MT5_REPORT_POSITIONS_REQUIRED, "MT5 positions report")

    out = pd.DataFrame(
        {
            "source": "mt5",
            "trade_id": df["Position"],
            "symbol_raw": df["Symbol"],
            "symbol_norm": df["Symbol"].map(lambda s: normalize_symbol(str(s), symbol_map)),
            "side": df["Type"].map(lambda s: normalize_side(str(s))),
            "qty": df["Volume"],
            "entry_time_utc": to_utc_naive(pd.to_datetime(df["Time"], errors="coerce"), mt5_timezone),
            "exit_time_utc": to_utc_naive(pd.to_datetime(df["Time.1"], errors="coerce"), mt5_timezone),
            "entry_price": df["Price"],
            "exit_price": df["Price.1"],
            "net_profit": df["Profit"],
        }
    )
    return coerce_normalized(out)


def _load_ctrader_closed_trades_xlsx(df: pd.DataFrame, symbol_map: dict[str, str], ctrader_timezone: str) -> pd.DataFrame:
    ensure_columns(df, CTRADER_CLOSED_TRADES_REQUIRED, "cTrader closed trades")

    open_dt = pd.to_datetime(df["Opening time"], errors="coerce", dayfirst=True)
    close_dt = pd.to_datetime(df["Closing time"], errors="coerce", dayfirst=True)
    qty_col = "Closing Quantity" if "Closing Quantity" in df.columns else "Closing volume"

    out = pd.DataFrame(
        {
            "source": "ctrader",
            "trade_id": pd.Series(range(1, len(df) + 1), index=df.index),
            "symbol_raw": df["Symbol"],
            "symbol_norm": df["Symbol"].map(lambda s: normalize_symbol(str(s), symbol_map)),
            "side": df["Opening direction"].map(lambda s: normalize_side(str(s))),
            "qty": pd.to_numeric(df[qty_col], errors="coerce"),
            "entry_time_utc": to_utc_naive(open_dt, ctrader_timezone),
            "exit_time_utc": to_utc_naive(close_dt, ctrader_timezone),
            "entry_price": pd.to_numeric(df["Entry price"], errors="coerce"),
            "exit_price": pd.to_numeric(df["Closing price"], errors="coerce"),
            "net_profit": pd.to_numeric(df["Net $"], errors="coerce"),
        }
    )
    return coerce_normalized(out)


def load_mt5_xlsx(file_obj, symbol_map: dict[str, str], mt5_timezone: str = "UTC", ctrader_timezone: str = "UTC") -> pd.DataFrame:
    df = pd.read_excel(file_obj)
    if all(col in df.columns for col in MT5_REQUIRED):
        return _load_mt5_closed_trades_xlsx(df, symbol_map, mt5_timezone)
    if all(col in df.columns for col in MT5_DEALS_REQUIRED):
        return _load_mt5_deals_xlsx(df, symbol_map, mt5_timezone)
    if all(col in df.columns for col in MT5_MINIMAL_DEALS_REQUIRED):
        return _load_mt5_minimal_deals_xlsx(df, symbol_map, mt5_timezone)
    if all(col in df.columns for col in CTRADER_CLOSED_TRADES_REQUIRED):
        return _load_ctrader_closed_trades_xlsx(df, symbol_map, ctrader_timezone)
    header_row = _find_report_header_row(file_obj)
    if header_row is not None:
        report_df = pd.read_excel(file_obj, header=header_row)
        if all(col in report_df.columns for col in MT5_REPORT_POSITIONS_REQUIRED):
            return _load_mt5_positions_report_xlsx(report_df, symbol_map, mt5_timezone)
    raise ValueError(
        "Unsupported MT5 XLSX schema. Expected closed-trades columns, deals columns, or positions-report columns. "
        f"Found columns: {list(df.columns)}"
    )
