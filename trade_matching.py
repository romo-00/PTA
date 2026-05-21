from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MatchConfig:
    matching_mode: str = "time"
    quantity_mode: str = "ignore for matching"
    quantity_factor: float = 1.0
    nt_entry_shift_seconds: int = 0
    strict_entry_tolerance_seconds: int = 20
    strict_exit_tolerance_seconds: int = 20
    entry_price_tolerance: float = 0.0
    exit_price_tolerance: float = 0.0
    hybrid_price_fallback_max_entry_delta_seconds: int = 86400


def _signed_delta(raw_delta: float, side: str) -> float:
    return raw_delta if side == "BUY" else -raw_delta


def _entry_advantage_points(nt_entry: float, mt_entry: float, side: str) -> float:
    # Positive means the target/MT5 entry was better than the source entry.
    return _signed_delta(nt_entry - mt_entry, side)


def _exit_advantage_points(nt_exit: float, mt_exit: float, side: str) -> float:
    # Positive means the target/MT5 exit was better than the source exit.
    return _signed_delta(mt_exit - nt_exit, side)


def _apply_quantity_mode(nt_df: pd.DataFrame, mt5_df: pd.DataFrame, mode: str, factor: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    nt_out = nt_df.copy()
    mt5_out = mt5_df.copy()
    if mode == "convert using factor":
        mt5_out["qty"] = mt5_out["qty"] * factor
    return nt_out, mt5_out


def _to_ts(value) -> pd.Timestamp:
    return pd.to_datetime(value, errors="coerce")


def _to_num(value) -> float:
    num = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(num) if pd.notna(num) else float("nan")


def _abs_seconds(a, b) -> float:
    ta = _to_ts(a)
    tb = _to_ts(b)
    if pd.isna(ta) or pd.isna(tb):
        return float("inf")
    return abs((ta - tb).total_seconds())


def _abs_price_delta(a, b) -> float:
    na = _to_num(a)
    nb = _to_num(b)
    if pd.isna(na) or pd.isna(nb):
        return float("inf")
    return abs(na - nb)


def _price_within_tolerance(delta: float, tolerance: float) -> bool:
    if tolerance < 0:
        return False
    return delta <= tolerance


def _validate_trade_lifecycle(row: pd.Series, label: str) -> str | None:
    entry = _to_ts(row.get("entry_time_utc"))
    exit_ = _to_ts(row.get("exit_time_utc"))
    if pd.isna(entry) or pd.isna(exit_):
        return f"INVALID_LIFECYCLE({label}_missing_entry_or_exit_time)"
    if entry > exit_:
        return f"INVALID_LIFECYCLE({label}_entry_after_exit)"
    return None


def _build_invalid_row(row: pd.Series, match_status: str, reason: str) -> dict:
    out = row.to_dict()
    out["match_status"] = match_status
    out["reason"] = reason
    return out


def _build_match_row(
    nt: pd.Series,
    mt: pd.Series,
    config: MatchConfig,
    *,
    match_type: str,
    notes: str,
    entry_delta_s: float,
    exit_delta_s: float,
    entry_price_delta_abs: float,
    exit_price_delta_abs: float,
) -> dict:
    entry_diff = _entry_advantage_points(nt["entry_price"], mt["entry_price"], nt["side"])
    exit_diff = _exit_advantage_points(nt["exit_price"], mt["exit_price"], nt["side"])
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
        "entry_price_delta_abs": entry_price_delta_abs,
        "exit_price_delta_abs": exit_price_delta_abs,
        "entry_time_delta_seconds": entry_delta_s,
        "exit_time_delta_seconds": exit_delta_s,
        "nt_points": nt_points,
        "mt5_points": mt5_points,
        "points_delta": mt5_points - nt_points,
        "qty_delta": mt["qty"] - nt["qty"] if config.quantity_mode == "convert using factor" else pd.NA,
        "net_profit_delta": net_profit_delta,
        "match_type": match_type,
        "match_status": "MATCHED_STRICT",
        "notes": notes,
    }


def _build_mismatch_rows(src: pd.Series, tgt: pd.Series, *, reason_code: str, entry_delta_s: float, exit_delta_s: float, entry_price_delta_abs: float, exit_price_delta_abs: float) -> tuple[dict, dict]:
    src_row = src.to_dict()
    tgt_row = tgt.to_dict()
    detail = (
        f"{reason_code}(entry_delta_s={entry_delta_s:.1f}, exit_delta_s={exit_delta_s:.1f}, "
        f"entry_price_delta={entry_price_delta_abs:.5f}, exit_price_delta={exit_price_delta_abs:.5f})"
    )
    src_row["match_status"] = reason_code
    tgt_row["match_status"] = reason_code
    src_row["reason"] = detail
    tgt_row["reason"] = detail
    src_row["mismatch_partner_trade_id"] = str(tgt.get("trade_id", ""))
    tgt_row["mismatch_partner_trade_id"] = str(src.get("trade_id", ""))
    return src_row, tgt_row


def _candidate_metrics(src: pd.Series, tgt: pd.Series, config: MatchConfig) -> dict[str, float | bool | str]:
    entry_delta_s = _abs_seconds(src.get("entry_time_utc"), tgt.get("entry_time_utc"))
    exit_delta_s = _abs_seconds(src.get("exit_time_utc"), tgt.get("exit_time_utc"))
    entry_price_delta_abs = _abs_price_delta(src.get("entry_price"), tgt.get("entry_price"))
    exit_price_delta_abs = _abs_price_delta(src.get("exit_price"), tgt.get("exit_price"))

    entry_time_ok = entry_delta_s <= float(config.strict_entry_tolerance_seconds)
    exit_time_ok = exit_delta_s <= float(config.strict_exit_tolerance_seconds)
    price_entry_ok = _price_within_tolerance(entry_price_delta_abs, float(config.entry_price_tolerance))
    price_exit_ok = _price_within_tolerance(exit_price_delta_abs, float(config.exit_price_tolerance))
    hybrid_price_fallback_time_ok = entry_delta_s <= float(config.hybrid_price_fallback_max_entry_delta_seconds)

    return {
        "entry_delta_s": entry_delta_s,
        "exit_delta_s": exit_delta_s,
        "entry_price_delta_abs": entry_price_delta_abs,
        "exit_price_delta_abs": exit_price_delta_abs,
        "entry_time_ok": entry_time_ok,
        "exit_time_ok": exit_time_ok,
        "time_full_ok": entry_time_ok and exit_time_ok,
        "price_full_ok": price_entry_ok and price_exit_ok,
        "hybrid_price_fallback_time_ok": hybrid_price_fallback_time_ok,
    }


def _mode_sort_key(mode: str, metrics: dict[str, float | bool | str]) -> tuple:
    entry_delta_s = float(metrics["entry_delta_s"])
    exit_delta_s = float(metrics["exit_delta_s"])
    entry_price_delta_abs = float(metrics["entry_price_delta_abs"])
    exit_price_delta_abs = float(metrics["exit_price_delta_abs"])
    total_time = entry_delta_s + exit_delta_s
    total_price = entry_price_delta_abs + exit_price_delta_abs

    if mode == "price":
        return (total_price, entry_price_delta_abs, exit_price_delta_abs, total_time, entry_delta_s)

    if mode == "hybrid":
        if bool(metrics["time_full_ok"]) and bool(metrics["price_full_ok"]):
            rank = 0
        elif bool(metrics["price_full_ok"]):
            rank = 1
        elif bool(metrics["time_full_ok"]):
            rank = 2
        elif bool(metrics["entry_time_ok"]):
            rank = 3
        else:
            rank = 4
        return (rank, total_price, total_time, entry_price_delta_abs, entry_delta_s)

    return (entry_delta_s, exit_delta_s, total_price, entry_price_delta_abs)


def _candidate_allowed(mode: str, metrics: dict[str, float | bool | str]) -> bool:
    if mode == "price":
        return bool(metrics["price_full_ok"])
    if mode == "hybrid":
        return bool((metrics["price_full_ok"] and metrics["hybrid_price_fallback_time_ok"]) or metrics["entry_time_ok"])
    return bool(metrics["entry_time_ok"])


def _match_result_for_candidate(mode: str, metrics: dict[str, float | bool | str]) -> tuple[str, str] | None:
    if mode == "price":
        if bool(metrics["price_full_ok"]):
            return ("price_tolerance", "Matched using entry/exit price tolerance.")
        return None

    if mode == "hybrid":
        if bool(metrics["time_full_ok"]) and bool(metrics["price_full_ok"]):
            return ("hybrid_time_price", "Matched using both time and price tolerances.")
        if bool(metrics["price_full_ok"]) and bool(metrics["hybrid_price_fallback_time_ok"]):
            return ("hybrid_price_fallback", "Matched by price tolerance fallback.")
        if bool(metrics["time_full_ok"]):
            return ("hybrid_time_only", "Matched using time tolerance only.")
        return None

    if bool(metrics["time_full_ok"]):
        return ("strict_tolerance", "Matched using entry/exit time tolerances.")
    return None


def _build_unmatched_reason(mode: str) -> str:
    if mode == "price":
        return "NO_PRICE_CANDIDATE_WITHIN_TOLERANCE"
    if mode == "hybrid":
        return "NO_TIME_OR_PRICE_CANDIDATE"
    return "NO_ENTRY_CANDIDATE_WITHIN_TOLERANCE"


def match_trades(
    nt_df: pd.DataFrame,
    mt5_df: pd.DataFrame,
    config: MatchConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    nt_adj, mt5_adj = _apply_quantity_mode(nt_df, mt5_df, config.quantity_mode, config.quantity_factor)

    nt_adj = nt_adj.copy()
    mt5_adj = mt5_adj.copy()
    nt_adj["entry_time_utc"] = nt_adj["entry_time_utc"] + pd.to_timedelta(config.nt_entry_shift_seconds, unit="s")

    matched_rows: list[dict] = []
    unmatched_source_rows: list[dict] = []
    unmatched_target_rows: list[dict] = []

    valid_source_rows: list[dict] = []
    valid_target_rows: list[dict] = []

    for _, src in nt_adj.iterrows():
        reason = _validate_trade_lifecycle(src, "source")
        if reason:
            unmatched_source_rows.append(_build_invalid_row(src, "UNMATCHED_SOURCE", reason))
        else:
            valid_source_rows.append(src.to_dict())

    for _, tgt in mt5_adj.iterrows():
        reason = _validate_trade_lifecycle(tgt, "target")
        if reason:
            unmatched_target_rows.append(_build_invalid_row(tgt, "UNMATCHED_TARGET", reason))
        else:
            valid_target_rows.append(tgt.to_dict())

    source_sorted = pd.DataFrame(valid_source_rows).sort_values("entry_time_utc").reset_index(drop=True) if valid_source_rows else pd.DataFrame()
    target_sorted = pd.DataFrame(valid_target_rows).sort_values("entry_time_utc").reset_index(drop=True) if valid_target_rows else pd.DataFrame()

    used_target: set[int] = set()
    mode = str(config.matching_mode or "time").strip().lower()

    for _, src in source_sorted.iterrows():
        candidates: list[tuple[tuple, int, pd.Series, dict[str, float | bool | str]]] = []
        for tgt_idx, tgt in target_sorted.iterrows():
            if tgt_idx in used_target:
                continue
            if str(src.get("symbol_norm", "")) != str(tgt.get("symbol_norm", "")):
                continue
            if str(src.get("side", "")) != str(tgt.get("side", "")):
                continue

            metrics = _candidate_metrics(src, tgt, config)
            if not _candidate_allowed(mode, metrics):
                continue
            candidates.append((_mode_sort_key(mode, metrics), int(tgt_idx), tgt, metrics))

        if not candidates:
            src_row = src.to_dict()
            src_row["match_status"] = "UNMATCHED_SOURCE"
            src_row["reason"] = _build_unmatched_reason(mode)
            unmatched_source_rows.append(src_row)
            continue

        candidates.sort(key=lambda x: x[0])
        _, best_tgt_idx, best_tgt, best_metrics = candidates[0]
        match_result = _match_result_for_candidate(mode, best_metrics)

        if match_result is not None:
            match_type, notes = match_result
            matched_rows.append(
                _build_match_row(
                    src,
                    best_tgt,
                    config,
                    match_type=match_type,
                    notes=notes,
                    entry_delta_s=float(best_metrics["entry_delta_s"]),
                    exit_delta_s=float(best_metrics["exit_delta_s"]),
                    entry_price_delta_abs=float(best_metrics["entry_price_delta_abs"]),
                    exit_price_delta_abs=float(best_metrics["exit_price_delta_abs"]),
                )
            )
            used_target.add(best_tgt_idx)
            continue

        if mode == "hybrid" and bool(best_metrics["entry_time_ok"]):
            src_row, tgt_row = _build_mismatch_rows(
                src,
                best_tgt,
                reason_code="EXIT_TIME_MISMATCH",
                entry_delta_s=float(best_metrics["entry_delta_s"]),
                exit_delta_s=float(best_metrics["exit_delta_s"]),
                entry_price_delta_abs=float(best_metrics["entry_price_delta_abs"]),
                exit_price_delta_abs=float(best_metrics["exit_price_delta_abs"]),
            )
            unmatched_source_rows.append(src_row)
            unmatched_target_rows.append(tgt_row)
            used_target.add(best_tgt_idx)
            continue

        src_row = src.to_dict()
        src_row["match_status"] = "UNMATCHED_SOURCE"
        src_row["reason"] = _build_unmatched_reason(mode)
        unmatched_source_rows.append(src_row)

    for tgt_idx, tgt in target_sorted.iterrows():
        if tgt_idx in used_target:
            continue
        tgt_row = tgt.to_dict()
        tgt_row["match_status"] = "UNMATCHED_TARGET"
        tgt_row["reason"] = "NO_CANDIDATE"
        unmatched_target_rows.append(tgt_row)

    matched_df = pd.DataFrame(matched_rows)
    unmatched_source_df = pd.DataFrame(unmatched_source_rows)
    unmatched_target_df = pd.DataFrame(unmatched_target_rows)

    return matched_df, unmatched_source_df, unmatched_target_df
