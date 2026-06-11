from __future__ import annotations

import pandas as pd
import pytest

from pdf_report import generate_post_trade_pdf


def test_generate_post_trade_pdf_returns_pdf_bytes():
    pytest.importorskip("reportlab")

    source = pd.DataFrame(
        [
            {
                "source": "mt5_backtest",
                "trade_id": "s1",
                "symbol_norm": "AUS200",
                "side": "BUY",
                "qty": 10,
                "entry_time_utc": pd.Timestamp("2026-06-01 00:00:00"),
                "exit_time_utc": pd.Timestamp("2026-06-01 00:30:00"),
                "entry_price": 100.0,
                "exit_price": 110.0,
            }
        ]
    )
    target = pd.DataFrame(
        [
            {
                "source": "mt5_live",
                "trade_id": "t1",
                "symbol_norm": "AUS200",
                "side": "BUY",
                "qty": 10,
                "entry_time_utc": pd.Timestamp("2026-06-01 00:00:01"),
                "exit_time_utc": pd.Timestamp("2026-06-01 00:30:01"),
                "entry_price": 101.0,
                "exit_price": 109.0,
            }
        ]
    )
    matched = pd.DataFrame(
        [
            {
                "symbol_norm": "AUS200",
                "side": "BUY",
                "nt_trade_id": "s1",
                "mt5_trade_id": "t1",
                "nt_entry_time_utc": pd.Timestamp("2026-06-01 00:00:00"),
                "mt5_entry_time_utc": pd.Timestamp("2026-06-01 00:00:01"),
                "nt_exit_time_utc": pd.Timestamp("2026-06-01 00:30:00"),
                "mt5_exit_time_utc": pd.Timestamp("2026-06-01 00:30:01"),
                "nt_points": 10.0,
                "mt5_points": 8.0,
                "points_delta": -2.0,
                "nt_qty": 10,
                "mt5_qty": 10,
                "model_to_live_entry_difference_pts": -1.0,
                "model_to_live_exit_difference_pts": -1.0,
                "entry_time_delta_seconds": 1,
                "exit_time_delta_seconds": 1,
                "net_profit_delta": -20.0,
            }
        ]
    )
    name, pdf = generate_post_trade_pdf(
        {
            "source_norm": source,
            "target_norm": target,
            "matched": matched,
            "unmatched_source": pd.DataFrame(),
            "unmatched_target": pd.DataFrame(),
            "settings": {
                "left_filename": "source.html",
                "right_filename": "target.html",
                "date_filter_start": "2026-06-01",
                "date_filter_end": "2026-06-01",
                "symbol_filter": ["AUS200"],
                "aud_rate_map": {"2026-06": 1.409388},
            },
        },
        mt5_timezone="Europe/Helsinki",
    )

    assert name == "source_vs_target_pta_report.pdf"
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000
