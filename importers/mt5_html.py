from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from normalization import coerce_normalized, normalize_side, normalize_symbol, to_utc_naive


def _clean(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _first_non_empty(values: list[object]) -> str:
    for value in values:
        text = str(value).strip()
        if text and text.lower() != "nan":
            return text
    return ""


def _is_blank_row(values: list[object]) -> bool:
    return _first_non_empty(values) == ""


def _make_unique_headers(values: list[object]) -> list[str]:
    seen: dict[str, int] = {}
    headers: list[str] = []
    for idx, value in enumerate(values):
        base = str(value).strip()
        if not base or base.lower() == "nan":
            base = f"col_{idx}"
        count = seen.get(base, 0)
        headers.append(base if count == 0 else f"{base}.{count}")
        seen[base] = count + 1
    return headers


def _to_num(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(s, errors="coerce")


def _to_volume_num(series: pd.Series) -> pd.Series:
    def first_num(v: object) -> float:
        m = re.search(r"-?\d+(?:\.\d+)?", str(v))
        return float(m.group(0)) if m else float("nan")

    return series.map(first_num)


def _to_nullable_int(series: pd.Series) -> pd.Series:
    num = _to_num(series)
    integral = num.where(num.isna() | ((num % 1).abs() < 1e-9))
    return integral.round().astype("Int64")


def _build_lookup(df: pd.DataFrame) -> dict[str, str]:
    return {_clean(col): str(col) for col in df.columns}


def _find_col(lookup: dict[str, str], aliases: list[str]) -> str | None:
    for alias in aliases:
        if alias in lookup:
            return lookup[alias]
    return None


@dataclass
class DealsCandidate:
    table_idx: int
    caption: str
    headers: list[str]
    df: pd.DataFrame


@dataclass
class ClosedTransactionsCandidate:
    table_idx: int
    caption: str
    headers: list[str]
    df: pd.DataFrame


def _extract_deals_sections(tables: list[pd.DataFrame]) -> tuple[list[DealsCandidate], list[tuple[int, str, list[str]]]]:
    candidates: list[DealsCandidate] = []
    summaries: list[tuple[int, str, list[str]]] = []
    section_markers = {"orders", "deals", "positions"}

    for table_idx, table in enumerate(tables):
        raw = table.copy()
        raw.columns = [str(c) for c in raw.columns]
        caption = _first_non_empty(raw.iloc[0].tolist()) if not raw.empty else f"table_{table_idx}"
        summaries.append((table_idx, caption, [str(c) for c in raw.columns]))

        row_idx = 0
        while row_idx < len(raw):
            marker = _clean(_first_non_empty(raw.iloc[row_idx].tolist()))
            if marker != "deals":
                row_idx += 1
                continue

            header_idx = row_idx + 1
            while header_idx < len(raw) and _is_blank_row(raw.iloc[header_idx].tolist()):
                header_idx += 1
            if header_idx >= len(raw):
                break

            next_marker = len(raw)
            scan = header_idx + 1
            while scan < len(raw):
                scan_marker = _clean(_first_non_empty(raw.iloc[scan].tolist()))
                if scan_marker in section_markers:
                    next_marker = scan
                    break
                scan += 1

            section_df = raw.iloc[header_idx + 1 : next_marker].copy()
            if not section_df.empty:
                headers = _make_unique_headers(raw.iloc[header_idx].tolist())
                section_df.columns = headers
                section_df = section_df[~section_df.apply(lambda r: _is_blank_row(r.tolist()), axis=1)].reset_index(drop=True)
                candidates.append(
                    DealsCandidate(
                        table_idx=table_idx,
                        caption="Deals",
                        headers=headers,
                        df=section_df,
                    )
                )
            row_idx = next_marker

    return candidates, summaries


def _extract_closed_transactions_sections(tables: list[pd.DataFrame]) -> list[ClosedTransactionsCandidate]:
    candidates: list[ClosedTransactionsCandidate] = []
    stop_markers = {
        "working_orders",
        "orders",
        "positions",
        "deals",
        "open_trades",
        "total_trades",
    }

    for table_idx, table in enumerate(tables):
        raw = table.copy()
        raw.columns = [str(c) for c in raw.columns]
        row_idx = 0
        while row_idx < len(raw):
            marker = _clean(_first_non_empty(raw.iloc[row_idx].tolist()))
            if marker not in {"closed_transactions", "closed_transactions_"}:
                row_idx += 1
                continue

            header_idx = row_idx + 1
            while header_idx < len(raw) and _is_blank_row(raw.iloc[header_idx].tolist()):
                header_idx += 1
            if header_idx >= len(raw):
                break

            next_marker = len(raw)
            scan = header_idx + 1
            while scan < len(raw):
                scan_marker = _clean(_first_non_empty(raw.iloc[scan].tolist()))
                if scan_marker in stop_markers:
                    next_marker = scan
                    break
                scan += 1

            section_df = raw.iloc[header_idx + 1 : next_marker].copy()
            if not section_df.empty:
                headers = _make_unique_headers(raw.iloc[header_idx].tolist())
                section_df.columns = headers
                section_df = section_df[~section_df.apply(lambda r: _is_blank_row(r.tolist()), axis=1)].reset_index(drop=True)
                candidates.append(
                    ClosedTransactionsCandidate(
                        table_idx=table_idx,
                        caption="Closed Transactions",
                        headers=headers,
                        df=section_df,
                    )
                )
            row_idx = next_marker

    return candidates


def _has_required_deals_headers(headers: list[str]) -> tuple[bool, int]:
    normalized = {_clean(h) for h in headers}
    required = {"time", "symbol", "type", "direction", "volume", "price"}
    header_hits = sum(1 for name in required if any(h == name or h.startswith(f"{name}_") for h in normalized))
    id_hits = int(any(h == "deal" or h.startswith("deal_") for h in normalized) or any(h == "order" or h.startswith("order_") for h in normalized))
    return (header_hits == len(required) and id_hits == 1), header_hits + id_hits


def _select_deals_table(candidates: list[DealsCandidate], summaries: list[tuple[int, str, list[str]]]) -> DealsCandidate:
    ranked: list[tuple[int, DealsCandidate]] = []
    for candidate in candidates:
        ok, score = _has_required_deals_headers(candidate.headers)
        if ok:
            ranked.append((score, candidate))

    if ranked:
        ranked.sort(key=lambda x: x[0], reverse=True)
        return ranked[0][1]

    detail = "; ".join([f"table={idx} caption={cap!r} headers={hdrs}" for idx, cap, hdrs in summaries])
    raise ValueError(f"No Deals table found with required headers. Available tables: {detail}")


def _select_closed_transactions_table(
    candidates: list[ClosedTransactionsCandidate], summaries: list[tuple[int, str, list[str]]]
) -> ClosedTransactionsCandidate:
    ranked: list[tuple[int, ClosedTransactionsCandidate]] = []
    required = {"ticket", "open_time", "type", "size", "item", "close_time", "profit"}
    for candidate in candidates:
        normalized = {_clean(h) for h in candidate.headers}
        score = sum(1 for r in required if r in normalized)
        if score >= 6:
            ranked.append((score, candidate))

    if ranked:
        ranked.sort(key=lambda x: x[0], reverse=True)
        return ranked[0][1]

    detail = "; ".join([f"table={idx} caption={cap!r} headers={hdrs}" for idx, cap, hdrs in summaries])
    raise ValueError(f"No Closed Transactions table found with required headers. Available tables: {detail}")


def _normalize_deals_rows(df: pd.DataFrame, source_label: str) -> pd.DataFrame:
    lookup = _build_lookup(df)
    time_col = _find_col(lookup, ["time"])
    symbol_col = _find_col(lookup, ["symbol", "instrument"])
    type_col = _find_col(lookup, ["type"])
    direction_col = _find_col(lookup, ["direction", "entry", "in_out"])
    volume_col = _find_col(lookup, ["volume", "lot", "lots", "qty", "quantity", "size"])
    price_col = _find_col(lookup, ["price"])
    order_col = _find_col(lookup, ["order", "order_id"])
    deal_col = _find_col(lookup, ["deal", "deal_id", "ticket", "id"])
    comment_col = _find_col(lookup, ["comment", "comments", "remark", "remarks"])
    profit_col = _find_col(lookup, ["profit", "net_profit", "pnl", "result"])
    commission_col = _find_col(lookup, ["commission"])
    swap_col = _find_col(lookup, ["swap"])

    required = [time_col, symbol_col, type_col, direction_col, volume_col, price_col]
    if any(col is None for col in required):
        cols = [str(c) for c in df.columns]
        raise ValueError(f"Deals table missing required columns. Found headers: {cols}")

    out = pd.DataFrame(
        {
            "time": pd.to_datetime(df[time_col], errors="coerce"),
            "symbol": df[symbol_col].astype(str).str.strip(),
            "side_type": df[type_col].astype(str).str.strip(),
            "direction": df[direction_col].astype(str).str.strip().str.lower(),
            "volume": _to_volume_num(df[volume_col]),
            "price": _to_num(df[price_col]),
            "order_id": _to_nullable_int(df[order_col]) if order_col else pd.Series(pd.array([pd.NA] * len(df), dtype="Int64")),
            "deal_id": _to_nullable_int(df[deal_col]) if deal_col else pd.Series(pd.array([pd.NA] * len(df), dtype="Int64")),
            "comment": df[comment_col].astype(str).fillna("") if comment_col else "",
            "profit": _to_num(df[profit_col]) if profit_col else 0.0,
            "commission": _to_num(df[commission_col]) if commission_col else 0.0,
            "swap": _to_num(df[swap_col]) if swap_col else 0.0,
            "source_deal": source_label,
        }
    )
    out["side"] = out["side_type"].map(lambda s: normalize_side(str(s)))
    out = out[out["time"].notna() & out["price"].notna()].copy()
    out = out[out["symbol"].str.len() > 0].copy()
    out = out[out["side_type"].str.lower() != "balance"].copy()
    out = out[~out["comment"].str.contains("dividend", case=False, na=False)].copy()
    out = out[out["side"].isin(["BUY", "SELL"])].copy()
    return out.reset_index(drop=True)


def _normalize_closed_transactions_rows(
    df: pd.DataFrame, symbol_map: dict[str, str], mt5_timezone: str, source_label: str
) -> pd.DataFrame:
    lookup = _build_lookup(df)
    ticket_col = _find_col(lookup, ["ticket", "order", "deal"])
    open_time_col = _find_col(lookup, ["open_time"])
    close_time_col = _find_col(lookup, ["close_time"])
    symbol_col = _find_col(lookup, ["item", "symbol", "instrument"])
    type_col = _find_col(lookup, ["type"])
    qty_col = _find_col(lookup, ["size", "volume", "qty", "quantity"])
    entry_price_col = _find_col(lookup, ["price", "open_price"])
    exit_price_col = _find_col(lookup, ["price_1", "close_price"])
    commission_col = _find_col(lookup, ["commission"])
    taxes_col = _find_col(lookup, ["taxes", "tax"])
    swap_col = _find_col(lookup, ["swap"])
    profit_col = _find_col(lookup, ["profit"])

    required = [open_time_col, close_time_col, symbol_col, type_col, qty_col, entry_price_col, exit_price_col]
    if any(col is None for col in required):
        cols = [str(c) for c in df.columns]
        raise ValueError(f"Closed Transactions table missing required columns. Found headers: {cols}")

    out = pd.DataFrame(
        {
            "source": source_label,
            "trade_id": df[ticket_col].astype(str).str.strip() if ticket_col else "",
            "symbol_raw": df[symbol_col].astype(str).str.strip(),
            "side": df[type_col].astype(str).map(lambda s: normalize_side(str(s))),
            "qty": _to_volume_num(df[qty_col]),
            "entry_time_utc": to_utc_naive(pd.to_datetime(df[open_time_col], errors="coerce"), mt5_timezone),
            "exit_time_utc": to_utc_naive(pd.to_datetime(df[close_time_col], errors="coerce"), mt5_timezone),
            "entry_price": _to_num(df[entry_price_col]),
            "exit_price": _to_num(df[exit_price_col]),
            "commission": _to_num(df[commission_col]) if commission_col else 0.0,
            "taxes": _to_num(df[taxes_col]) if taxes_col else 0.0,
            "swap": _to_num(df[swap_col]) if swap_col else 0.0,
            "profit_raw": _to_num(df[profit_col]) if profit_col else 0.0,
        }
    )
    out["symbol_norm"] = out["symbol_raw"].map(lambda s: normalize_symbol(str(s), symbol_map))
    out["net_profit"] = out["profit_raw"].fillna(0.0) + out["commission"].fillna(0.0) + out["taxes"].fillna(0.0) + out["swap"].fillna(0.0)
    out = out[out["side"].isin(["BUY", "SELL"])].copy()
    out = out[out["symbol_raw"].str.len() > 0].copy()
    out = out[out["qty"].notna() & (out["qty"] > 0)].copy()
    out = out[out["entry_time_utc"].notna() & out["exit_time_utc"].notna()].copy()
    out = out[out["entry_price"].notna() & out["exit_price"].notna()].copy()
    return coerce_normalized(out).reset_index(drop=True)


def _reconstruct_closed_trades(deals: pd.DataFrame, symbol_map: dict[str, str], mt5_timezone: str, source_label: str) -> pd.DataFrame:
    if deals.empty:
        return pd.DataFrame()

    group_key = "order_id"
    if deals["order_id"].isna().all() and deals["deal_id"].notna().any():
        group_key = "deal_id"

    rows: list[dict] = []
    grouped = deals.groupby(group_key, dropna=False) if group_key in deals.columns else []
    for _, grp in grouped:
        g = grp.sort_values("time").copy()
        in_rows = g[g["direction"].str.contains("in|open|entry", regex=True, na=False)]
        out_rows = g[g["direction"].str.contains("out|close|exit", regex=True, na=False)]
        if in_rows.empty or out_rows.empty:
            continue

        entry = in_rows.iloc[0]
        exit_ = out_rows.iloc[-1]
        qty = min(float(in_rows["volume"].sum()), float(out_rows["volume"].sum()))
        if qty <= 0:
            continue

        rows.append(
            {
                "source": source_label,
                "trade_id": str(entry["order_id"]) if pd.notna(entry["order_id"]) else str(entry["deal_id"]),
                "symbol_raw": entry["symbol"],
                "symbol_norm": normalize_symbol(str(entry["symbol"]), symbol_map),
                "side": entry["side"],
                "qty": qty,
                "entry_time_utc": to_utc_naive(pd.Series([entry["time"]]), mt5_timezone).iloc[0],
                "exit_time_utc": to_utc_naive(pd.Series([exit_["time"]]), mt5_timezone).iloc[0],
                "entry_price": float(entry["price"]),
                "exit_price": float(exit_["price"]),
                "net_profit": float(g["profit"].sum() + g["commission"].sum() + g["swap"].sum()),
            }
        )

    if not rows:
        # FIFO fallback for tester-style deals where in/out are not on same order id.
        for symbol, grp in deals.sort_values("time").groupby("symbol"):
            queue: list[pd.Series] = []
            for _, row in grp.iterrows():
                d = str(row["direction"]).lower()
                if "in" in d or "open" in d or "entry" in d:
                    queue.append(row)
                    continue
                if not ("out" in d or "close" in d or "exit" in d):
                    continue
                match_idx = next((i for i, q in enumerate(queue) if q["side"] != row["side"]), None)
                if match_idx is None:
                    continue
                entry = queue.pop(match_idx)
                qty = min(float(entry["volume"]), float(row["volume"]))
                if qty <= 0:
                    continue
                rows.append(
                    {
                        "source": source_label,
                        "trade_id": str(entry["deal_id"]) if pd.notna(entry["deal_id"]) else f"{symbol}_{entry.name}",
                        "symbol_raw": entry["symbol"],
                        "symbol_norm": normalize_symbol(str(entry["symbol"]), symbol_map),
                        "side": entry["side"],
                        "qty": qty,
                        "entry_time_utc": to_utc_naive(pd.Series([entry["time"]]), mt5_timezone).iloc[0],
                        "exit_time_utc": to_utc_naive(pd.Series([row["time"]]), mt5_timezone).iloc[0],
                        "entry_price": float(entry["price"]),
                        "exit_price": float(row["price"]),
                        "net_profit": float(row["profit"] + row["commission"] + row["swap"]),
                    }
                )

    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("Deals rows loaded but no closed in/out trade pairs could be reconstructed.")
    out = coerce_normalized(out)
    out = out[out["side"].isin(["BUY", "SELL"])].copy()
    out = out[out["entry_time_utc"].notna() & out["exit_time_utc"].notna()].copy()
    out = out[out["entry_price"].notna() & out["exit_price"].notna()].copy()
    return out.reset_index(drop=True)


def load_mt5_html(file_obj, symbol_map: dict[str, str], mt5_timezone: str = "UTC", source_label: str = "mt5") -> pd.DataFrame:
    try:
        tables = pd.read_html(file_obj)
    except ImportError as exc:
        raise ImportError("MT5 HTML import requires 'lxml'. Install it with: py -m pip install lxml") from exc

    candidates, summaries = _extract_deals_sections(tables)
    trade_source = "mt5_backtest" if "backtest" in source_label.lower() else "mt5_live"
    if candidates:
        try:
            selected = _select_deals_table(candidates, summaries)
            deal_source = "mt5_backtest_deal" if "backtest" in source_label.lower() else "mt5_live_deal"
            deals = _normalize_deals_rows(selected.df, deal_source)
            return _reconstruct_closed_trades(deals, symbol_map, mt5_timezone, trade_source)
        except ValueError:
            pass

    closed_candidates = _extract_closed_transactions_sections(tables)
    if closed_candidates:
        selected_closed = _select_closed_transactions_table(closed_candidates, summaries)
        return _normalize_closed_transactions_rows(selected_closed.df, symbol_map, mt5_timezone, trade_source)

    detail = "; ".join([f"table={idx} caption={cap!r} headers={hdrs}" for idx, cap, hdrs in summaries])
    raise ValueError(
        "No supported MT5 trades table found. Expected Deals section or Closed Transactions section. "
        f"Available tables: {detail}"
    )
