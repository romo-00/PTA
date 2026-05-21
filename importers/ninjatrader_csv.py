from __future__ import annotations

from pathlib import Path

import pandas as pd

from normalization import coerce_normalized, ensure_columns, normalize_side, normalize_symbol, to_utc_naive

NT_TIMEZONE = "America/New_York"
NT_EXECUTION_TIMEZONE = "UTC"

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

NT_MINIMAL_REQUIRED = [
    "Market pos.",
    "Qty",
    "Entry price",
    "Exit price",
    "Entry time",
    "Exit time",
    "Profit",
]

NT_EXECUTION_REQUIRED = [
    "Symbol",
    "Action",
    "Quantity",
    "Price",
    "Time",
    "Date",
]


def _infer_symbol_from_filename(filename: str) -> str:
    stem = Path(filename or "").stem.strip()
    return stem or "UNKNOWN"


def _normalize_ninjatrader_df(df: pd.DataFrame, symbol_map: dict[str, str], filename: str = "") -> pd.DataFrame:
    if all(col in df.columns for col in NT_REQUIRED):
        trade_id = df["Trade number"]
        symbol_raw = df["Instrument"]
    else:
        ensure_columns(df, NT_MINIMAL_REQUIRED, "NinjaTrader")
        inferred_symbol = _infer_symbol_from_filename(filename)
        trade_id = pd.Series(range(1, len(df) + 1), index=df.index)
        symbol_raw = pd.Series([inferred_symbol] * len(df), index=df.index)

    entry_time_utc = to_utc_naive(df["Entry time"], NT_TIMEZONE)
    exit_time_utc = to_utc_naive(df["Exit time"], NT_TIMEZONE)

    out = pd.DataFrame(
        {
            "source": "ninjatrader",
            "trade_id": trade_id,
            "symbol_raw": symbol_raw,
            "symbol_norm": symbol_raw.map(lambda s: normalize_symbol(str(s), symbol_map)),
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


def _extract_order_ref_symbol(order_ref: object) -> str | None:
    text = str(order_ref or "")
    marker = "|100~"
    if marker not in text:
        return None
    remainder = text.split(marker, 1)[1]
    return remainder.split("|", 1)[0].strip() or None


def _normalize_execution_csv(df: pd.DataFrame, symbol_map: dict[str, str], filename: str = "") -> pd.DataFrame:
    ensure_columns(df, NT_EXECUTION_REQUIRED, "Execution CSV")

    work = df.copy()
    work["dt_local"] = pd.to_datetime(
        work["Date"].astype(str).str.strip() + " " + work["Time"].astype(str).str.strip(),
        format="%Y%m%d %H:%M:%S",
        errors="coerce",
    )
    work["qty_num"] = pd.to_numeric(work["Quantity"], errors="coerce")
    work["price_num"] = pd.to_numeric(work["Price"], errors="coerce")
    work["action_norm"] = work["Action"].astype(str).str.strip().str.upper()
    order_ref_series = work["Order Ref."] if "Order Ref." in work.columns else pd.Series(index=work.index, dtype="object")
    work["symbol_raw"] = order_ref_series.map(_extract_order_ref_symbol)
    work["symbol_raw"] = work["symbol_raw"].where(work["symbol_raw"].notna(), work["Symbol"].astype(str).str.strip())
    work = work[work["dt_local"].notna() & work["qty_num"].notna() & (work["qty_num"] > 0) & work["price_num"].notna()].copy()
    work = work[work["action_norm"].isin(["BOT", "SLD"])].copy()
    if work.empty:
        raise ValueError("Execution CSV did not contain any usable BOT/SLD fill rows.")

    work = work.sort_values(["dt_local"]).reset_index(drop=True)
    rows: list[dict[str, object]] = []
    trade_id = 1
    open_long: list[dict[str, object]] = []
    open_short: list[dict[str, object]] = []

    for rec in work.to_dict("records"):
        qty_remaining = int(rec["qty_num"])
        action = str(rec["action_norm"])
        symbol_raw = str(rec["symbol_raw"] or _infer_symbol_from_filename(filename))
        fill = {
            "time_utc": to_utc_naive(pd.Series([rec["dt_local"]]), NT_EXECUTION_TIMEZONE).iloc[0],
            "price": float(rec["price_num"]),
            "symbol_raw": symbol_raw,
        }

        if action == "BOT":
            while qty_remaining > 0 and open_short:
                opened = open_short.pop(0)
                rows.append(
                    {
                        "source": "ninjatrader",
                        "trade_id": trade_id,
                        "symbol_raw": opened["symbol_raw"],
                        "symbol_norm": normalize_symbol(str(opened["symbol_raw"]), symbol_map),
                        "side": "SELL",
                        "qty": 1.0,
                        "entry_time_utc": opened["time_utc"],
                        "exit_time_utc": fill["time_utc"],
                        "entry_price": opened["price"],
                        "exit_price": fill["price"],
                        "net_profit": opened["price"] - fill["price"],
                    }
                )
                trade_id += 1
                qty_remaining -= 1
            for _ in range(qty_remaining):
                open_long.append(fill.copy())
        else:
            while qty_remaining > 0 and open_long:
                opened = open_long.pop(0)
                rows.append(
                    {
                        "source": "ninjatrader",
                        "trade_id": trade_id,
                        "symbol_raw": opened["symbol_raw"],
                        "symbol_norm": normalize_symbol(str(opened["symbol_raw"]), symbol_map),
                        "side": "BUY",
                        "qty": 1.0,
                        "entry_time_utc": opened["time_utc"],
                        "exit_time_utc": fill["time_utc"],
                        "entry_price": opened["price"],
                        "exit_price": fill["price"],
                        "net_profit": fill["price"] - opened["price"],
                    }
                )
                trade_id += 1
                qty_remaining -= 1
            for _ in range(qty_remaining):
                open_short.append(fill.copy())

    if not rows:
        raise ValueError("Execution CSV did not reconstruct any closed trades.")
    return coerce_normalized(pd.DataFrame(rows))


def load_ninjatrader_csv(file_obj, symbol_map: dict[str, str]) -> pd.DataFrame:
    df = pd.read_csv(file_obj)
    if all(col in df.columns for col in NT_EXECUTION_REQUIRED):
        return _normalize_execution_csv(df, symbol_map)
    return _normalize_ninjatrader_df(df, symbol_map)


def load_ninjatrader_xlsx(file_obj, symbol_map: dict[str, str], filename: str = "") -> pd.DataFrame:
    df = pd.read_excel(file_obj)
    return _normalize_ninjatrader_df(df, symbol_map, filename=filename)


