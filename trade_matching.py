from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MatchConfig:
    quantity_mode: str = "ignore for matching"
    quantity_factor: float = 1.0
    nt_entry_shift_seconds: int = 0


def _signed_delta(raw_delta: float, side: str) -> float:
    return raw_delta if side == "BUY" else -raw_delta


def _apply_quantity_mode(nt_df: pd.DataFrame, mt5_df: pd.DataFrame, mode: str, factor: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    nt_out = nt_df.copy()
    mt5_out = mt5_df.copy()
    if mode == "convert using factor":
        mt5_out["qty"] = mt5_out["qty"] * factor
    return nt_out, mt5_out


def _build_match_row(nt: pd.Series, mt: pd.Series, config: MatchConfig) -> dict:
    entry_diff = _signed_delta(mt["entry_price"] - nt["entry_price"], nt["side"])
    exit_diff = _signed_delta(mt["exit_price"] - nt["exit_price"], nt["side"])
    nt_points = _signed_delta(nt["exit_price"] - nt["entry_price"], nt["side"])
    mt5_points = _signed_delta(mt["exit_price"] - mt["entry_price"], nt["side"])

    net_profit_delta = pd.NA
    if pd.notna(nt.get("net_profit")) and pd.notna(mt.get("net_profit")):
        net_profit_delta = mt["net_profit"] - nt["net_profit"]

    return {
        "symbol_norm": nt["symbol_norm"],
        "side": nt["side"],
        "nt_trade_id": nt["trade_id"],
        "mt5_trade_id": mt["trade_id"],
        "nt_entry_time_utc": nt["entry_time_utc"],
        "mt5_entry_time_utc": mt["entry_time_utc"],
        "nt_exit_time_utc": nt["exit_time_utc"],
        "mt5_exit_time_utc": mt["exit_time_utc"],
        "nt_entry_price": nt["entry_price"],
        "mt5_entry_price": mt["entry_price"],
        "nt_exit_price": nt["exit_price"],
        "mt5_exit_price": mt["exit_price"],
        "nt_qty": nt["qty"],
        "mt5_qty": mt["qty"],
        "model_to_live_entry_difference_pts": entry_diff,
        "model_to_live_exit_difference_pts": exit_diff,
        "nt_points": nt_points,
        "mt5_points": mt5_points,
        "points_delta": mt5_points - nt_points,
        "qty_delta": mt["qty"] - nt["qty"] if config.quantity_mode == "convert using factor" else pd.NA,
        "net_profit_delta": net_profit_delta,
        "match_type": "sequential",
        "notes": "",
    }


def match_trades(
    nt_df: pd.DataFrame,
    mt5_df: pd.DataFrame,
    config: MatchConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    nt_adj, mt5_adj = _apply_quantity_mode(nt_df, mt5_df, config.quantity_mode, config.quantity_factor)

    nt_adj = nt_adj.copy()
    nt_adj["entry_time_utc"] = nt_adj["entry_time_utc"] + pd.to_timedelta(config.nt_entry_shift_seconds, unit="s")

    nt_sorted = nt_adj.sort_values("entry_time_utc").reset_index(drop=True)
    mt5_sorted = mt5_adj.sort_values("entry_time_utc").reset_index(drop=True)

    pair_count = min(len(nt_sorted), len(mt5_sorted))
    matches: list[dict] = []

    for i in range(pair_count):
        nt = nt_sorted.iloc[i]
        mt = mt5_sorted.iloc[i]
        matches.append(_build_match_row(nt, mt, config))

    unmatched_nt = nt_sorted.iloc[pair_count:].copy()
    unmatched_mt5 = mt5_sorted.iloc[pair_count:].copy()
    if not unmatched_nt.empty:
        unmatched_nt["reason"] = "SEQUENCE_DRIFT"
    if not unmatched_mt5.empty:
        unmatched_mt5["reason"] = "SEQUENCE_DRIFT"

    return pd.DataFrame(matches), unmatched_nt, unmatched_mt5

