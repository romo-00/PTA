from __future__ import annotations

from io import BytesIO, StringIO

import pandas as pd

from importers.mt5_html import _reconstruct_closed_trades, load_mt5_html
from importers.mt5_xlsx import load_mt5_xlsx
from importers.ninjatrader_csv import load_ninjatrader_csv, load_ninjatrader_xlsx
from normalization import to_utc_naive
from trade_matching import MatchConfig, match_trades


def test_ninjatrader_timezone_normalization_to_utc():
    csv_data = StringIO(
        "Trade number,Instrument,Market pos.,Qty,Entry price,Exit price,Entry time,Exit time,Profit\n"
        "1,MNQH6,Long,1,21000,21020,1/2/2026 4:09,1/2/2026 4:39,100\n"
    )
    df = load_ninjatrader_csv(csv_data, {"MNQ*": "NAS100"})

    assert df.iloc[0]["entry_time_utc"] == pd.Timestamp("2026-01-02 09:09:00")
    assert df.iloc[0]["exit_time_utc"] == pd.Timestamp("2026-01-02 09:39:00")


def test_mt5_datetime_construction_from_date_time_columns():
    mt5 = pd.DataFrame(
        [
            {
                "Type": "deal",
                "Ticket": 101,
                "Symbol": "NAS100",
                "Lots": 1.0,
                "Buy/sell": "buy",
                "Open price": 21000,
                "Close price": 21010,
                "Open time": "12:30:15",
                "Close time": "13:00:15",
                "Open date": "2026-01-02",
                "Close date": "2026-01-02",
                "Profit": 10,
                "Swap": 0,
                "Commission": 0,
                "Net profit": 10,
                "T/P": "",
                "S/L": "",
                "Pips": 0,
                "Result": "win",
                "Trade duration (hours)": 0.5,
                "Magic number": 1,
                "Order comment": "",
                "Account": "A",
            }
        ]
    )
    bio = BytesIO()
    mt5.to_excel(bio, index=False)
    bio.seek(0)

    df = load_mt5_xlsx(bio, {"MNQ*": "NAS100"}, mt5_timezone="UTC")
    assert df.iloc[0]["entry_time_utc"] == pd.Timestamp("2026-01-02 12:30:15")
    assert df.iloc[0]["exit_time_utc"] == pd.Timestamp("2026-01-02 13:00:15")


def test_mt5_broker_dst_timezone_converts_winter_and_summer_correctly():
    ts = pd.Series(["2024-01-02 17:28:39", "2024-07-02 17:28:39"])
    out = to_utc_naive(ts, "Europe/Helsinki")

    assert out.iloc[0] == pd.Timestamp("2024-01-02 15:28:39")
    assert out.iloc[1] == pd.Timestamp("2024-07-02 14:28:39")


def test_mt5_html_importer_backtest_live_table():
    html = StringIO(
        """
        <html><body>
        <table>
          <tr><th>Ticket</th><th>Symbol</th><th>Buy/sell</th><th>Lots</th><th>Open time</th><th>Close time</th><th>Open price</th><th>Close price</th><th>Net profit</th></tr>
          <tr><td>2001</td><td>NAS100</td><td>buy</td><td>1.0</td><td>2026-01-02 10:00:00</td><td>2026-01-02 10:20:00</td><td>21000</td><td>21015</td><td>15</td></tr>
        </table>
        </body></html>
        """
    )

    df = load_mt5_html(html, {"MNQ*": "NAS100"}, mt5_timezone="UTC", source_label="mt5_backtest")
    assert len(df) == 1
    assert df.iloc[0]["symbol_norm"] == "NAS100"
    assert df.iloc[0]["side"] == "BUY"
    assert df.iloc[0]["entry_time_utc"] == pd.Timestamp("2026-01-02 10:00:00")
    assert df.iloc[0]["exit_time_utc"] == pd.Timestamp("2026-01-02 10:20:00")


def test_strict_matching_with_named_differences():
    nt = pd.DataFrame(
        [
            {
                "source": "ninjatrader",
                "trade_id": "1",
                "symbol_raw": "MNQH6",
                "symbol_norm": "NAS100",
                "side": "BUY",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-02 10:00:00"),
                "exit_time_utc": pd.Timestamp("2026-01-02 10:30:00"),
                "entry_price": 21000.0,
                "exit_price": 21020.0,
                "net_profit": 100.0,
            }
        ]
    )

    mt5 = pd.DataFrame(
        [
            {
                "source": "mt5",
                "trade_id": "101",
                "symbol_raw": "NAS100",
                "symbol_norm": "NAS100",
                "side": "BUY",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-02 10:00:10"),
                "exit_time_utc": pd.Timestamp("2026-01-02 10:30:10"),
                "entry_price": 21001.0,
                "exit_price": 21021.0,
                "net_profit": 95.0,
            }
        ]
    )

    matched, unmatched_nt, unmatched_mt5 = match_trades(nt, mt5, MatchConfig())

    assert len(matched) == 1
    assert unmatched_nt.empty
    assert unmatched_mt5.empty
    assert matched.iloc[0]["model_to_live_entry_difference_pts"] == -1.0
    assert matched.iloc[0]["model_to_live_exit_difference_pts"] == 1.0
    assert matched.iloc[0]["nt_points"] == 20.0
    assert matched.iloc[0]["mt5_points"] == 20.0
    assert matched.iloc[0]["points_delta"] == 0.0
    assert matched.iloc[0]["match_type"] == "strict_tolerance"


def test_atomic_entry_binding_preserves_trade_lifecycle_no_exit_swap():
    source = pd.DataFrame(
        [
            {
                "source": "mt5_backtest",
                "trade_id": "124206986",
                "symbol_raw": "XAUUSD",
                "symbol_norm": "XAUUSD",
                "side": "BUY",
                "qty": 0.04,
                "entry_time_utc": pd.Timestamp("2026-02-18 21:27:00"),
                "exit_time_utc": pd.Timestamp("2026-02-19 01:01:37"),
                "entry_price": 4988.20,
                "exit_price": 4960.76,
                "net_profit": -118.66,
            },
            {
                "source": "mt5_backtest",
                "trade_id": "124208328",
                "symbol_raw": "XAUUSD",
                "symbol_norm": "XAUUSD",
                "side": "BUY",
                "qty": 0.04,
                "entry_time_utc": pd.Timestamp("2026-02-18 21:28:00"),
                "exit_time_utc": pd.Timestamp("2026-02-18 21:35:00"),
                "entry_price": 4975.41,
                "exit_price": 4983.09,
                "net_profit": 30.63,
            },
        ]
    )

    target = pd.DataFrame(
        [
            {
                "source": "mt5_live",
                "trade_id": "51259612",
                "symbol_raw": "xauusd",
                "symbol_norm": "XAUUSD",
                "side": "BUY",
                "qty": 0.03,
                "entry_time_utc": pd.Timestamp("2026-02-18 21:27:00"),
                "exit_time_utc": pd.Timestamp("2026-02-19 01:01:37"),
                "entry_price": 4988.13,
                "exit_price": 4963.27,
                "net_profit": -81.33,
            },
            {
                "source": "mt5_live",
                "trade_id": "51259630",
                "symbol_raw": "xauusd",
                "symbol_norm": "XAUUSD",
                "side": "BUY",
                "qty": 0.03,
                "entry_time_utc": pd.Timestamp("2026-02-18 21:28:00"),
                "exit_time_utc": pd.Timestamp("2026-02-18 21:35:00"),
                "entry_price": 4975.48,
                "exit_price": 4983.09,
                "net_profit": 22.69,
            },
        ]
    )

    matched, unmatched_source, unmatched_target = match_trades(source, target, MatchConfig())

    assert len(matched) == 2
    assert unmatched_source.empty
    assert unmatched_target.empty

    pair_2127 = matched[matched["nt_trade_id"] == "124206986"].iloc[0]
    pair_2128 = matched[matched["nt_trade_id"] == "124208328"].iloc[0]

    assert str(pair_2127["mt5_trade_id"]) == "51259612"
    assert pd.Timestamp(pair_2127["mt5_exit_time_utc"]) == pd.Timestamp("2026-02-19 01:01:37")
    assert str(pair_2128["mt5_trade_id"]) == "51259630"
    assert pd.Timestamp(pair_2128["mt5_exit_time_utc"]) == pd.Timestamp("2026-02-18 21:35:00")


def test_mt5_deals_fallback_reconstruction_uses_recent_open_lifecycle_binding():
    deals = pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-02-18 21:27:00"),
                "symbol": "XAUUSD",
                "side_type": "buy",
                "direction": "in",
                "volume": 0.04,
                "price": 4988.20,
                "order_id": 1,
                "deal_id": 1001,
                "comment": "",
                "profit": 0.0,
                "commission": 0.0,
                "swap": 0.0,
                "side": "BUY",
            },
            {
                "time": pd.Timestamp("2026-02-18 21:28:00"),
                "symbol": "XAUUSD",
                "side_type": "buy",
                "direction": "in",
                "volume": 0.04,
                "price": 4975.41,
                "order_id": 2,
                "deal_id": 1002,
                "comment": "",
                "profit": 0.0,
                "commission": 0.0,
                "swap": 0.0,
                "side": "BUY",
            },
            {
                "time": pd.Timestamp("2026-02-18 21:35:00"),
                "symbol": "XAUUSD",
                "side_type": "sell",
                "direction": "out",
                "volume": 0.04,
                "price": 4983.09,
                "order_id": 3,
                "deal_id": 2001,
                "comment": "",
                "profit": 0.0,
                "commission": 0.0,
                "swap": 0.0,
                "side": "SELL",
            },
            {
                "time": pd.Timestamp("2026-02-19 01:01:37"),
                "symbol": "XAUUSD",
                "side_type": "sell",
                "direction": "out",
                "volume": 0.04,
                "price": 4960.76,
                "order_id": 4,
                "deal_id": 2002,
                "comment": "",
                "profit": 0.0,
                "commission": 0.0,
                "swap": 0.0,
                "side": "SELL",
            },
        ]
    )

    out = _reconstruct_closed_trades(deals, {"XAUUSD": "XAUUSD"}, "UTC", "mt5_backtest")
    out = out.sort_values("entry_time_utc").reset_index(drop=True)

    row_2127 = out[out["trade_id"] == "1001"].iloc[0]
    row_2128 = out[out["trade_id"] == "1002"].iloc[0]

    assert pd.Timestamp(row_2127["exit_time_utc"]) == pd.Timestamp("2026-02-19 01:01:37")
    assert pd.Timestamp(row_2128["exit_time_utc"]) == pd.Timestamp("2026-02-18 21:35:00")


def test_ninjatrader_xlsx_minimal_schema_infers_symbol_from_filename():
    nt = pd.DataFrame(
        [
            {
                "Market pos.": "Short",
                "Qty": 3,
                "Entry price": 87899.99,
                "Exit price": 87963.04,
                "Entry time": "2025-12-23 06:00:00",
                "Exit time": "2025-12-23 19:00:00",
                "Profit": -189.15,
            }
        ]
    )
    bio = BytesIO()
    nt.to_excel(bio, index=False)
    bio.seek(0)

    df = load_ninjatrader_xlsx(bio, {"BTC*": "BTCUSD"}, filename="BTC_60 & 240 NT.xlsx")
    assert df.iloc[0]["trade_id"] == "1"
    assert df.iloc[0]["symbol_norm"] == "BTCUSD"
    assert df.iloc[0]["side"] == "SELL"


def test_mt5_deals_xlsx_reconstructs_closed_trade():
    mt5 = pd.DataFrame(
        [
            {
                "Time": "2025.12.23 05:07:15",
                "Deal": 33249132,
                "Symbol": "BTCUSD.a",
                "Type": "sell",
                "Direction": "in",
                "Volume": 0.09,
                "Price": 87800.40,
                "Swap": 0.0,
                "Profit": 0.0,
                "Balance": 20000.0,
                "Comment": "BTC60",
            },
            {
                "Time": "2025.12.23 18:03:49",
                "Deal": 33256490,
                "Symbol": "BTCUSD.a",
                "Type": "buy",
                "Direction": "out",
                "Volume": 0.09,
                "Price": 88009.60,
                "Swap": 0.0,
                "Profit": -27.70,
                "Balance": 19972.30,
                "Comment": "",
            },
        ]
    )
    bio = BytesIO()
    mt5.to_excel(bio, index=False)
    bio.seek(0)

    df = load_mt5_xlsx(bio, {"BTCUSD*": "BTCUSD"}, mt5_timezone="UTC")
    assert len(df) == 1
    assert df.iloc[0]["symbol_norm"] == "BTCUSD"
    assert df.iloc[0]["side"] == "SELL"
    assert df.iloc[0]["entry_price"] == 87800.40
    assert df.iloc[0]["exit_price"] == 88009.60


def test_mt5_minimal_deals_xlsx_reconstructs_closed_trade_without_symbol():
    mt5 = pd.DataFrame(
        [
            {
                "Time": "2024.01.02 17:28:39",
                "Deal": 2,
                "Type": "sell",
                "Direction": "in",
                "Volume": 2.0,
                "Price": 16544.11,
                "Profit": 0.0,
                "Balance": 50000.0,
            },
            {
                "Time": "2024.01.02 20:31:00",
                "Deal": 3,
                "Type": "buy",
                "Direction": "out",
                "Volume": 2.0,
                "Price": 16470.86,
                "Profit": 146.5,
                "Balance": 50146.5,
            },
        ]
    )
    bio = BytesIO()
    mt5.to_excel(bio, index=False)
    bio.seek(0)

    df = load_mt5_xlsx(bio, {}, mt5_timezone="UTC")
    assert len(df) == 1
    assert df.iloc[0]["symbol_norm"] == "UNKNOWN"
    assert df.iloc[0]["side"] == "SELL"
    assert df.iloc[0]["entry_price"] == 16544.11
    assert df.iloc[0]["exit_price"] == 16470.86


def test_price_matching_mode_matches_on_prices_when_times_do_not():
    source = pd.DataFrame(
        [
            {
                "source": "ninjatrader",
                "trade_id": "1",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "BUY",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-02 10:00:00"),
                "exit_time_utc": pd.Timestamp("2026-01-02 11:00:00"),
                "entry_price": 100.0,
                "exit_price": 110.0,
                "net_profit": 10.0,
            }
        ]
    )
    target = pd.DataFrame(
        [
            {
                "source": "mt5",
                "trade_id": "101",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "BUY",
                "qty": 5.0,
                "entry_time_utc": pd.Timestamp("2026-01-03 15:00:00"),
                "exit_time_utc": pd.Timestamp("2026-01-03 16:00:00"),
                "entry_price": 100.05,
                "exit_price": 110.04,
                "net_profit": 50.0,
            }
        ]
    )

    matched, unmatched_source, unmatched_target = match_trades(
        source,
        target,
        MatchConfig(matching_mode="price", entry_price_tolerance=0.1, exit_price_tolerance=0.1),
    )

    assert len(matched) == 1
    assert unmatched_source.empty
    assert unmatched_target.empty
    assert matched.iloc[0]["match_type"] == "price_tolerance"
    assert matched.iloc[0]["nt_points"] == 10.0
    assert round(float(matched.iloc[0]["mt5_points"]), 2) == 9.99


def test_hybrid_matching_prefers_price_confirmed_candidate():
    source = pd.DataFrame(
        [
            {
                "source": "ninjatrader",
                "trade_id": "1",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "SELL",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-02 10:00:00"),
                "exit_time_utc": pd.Timestamp("2026-01-02 11:00:00"),
                "entry_price": 200.0,
                "exit_price": 180.0,
                "net_profit": 20.0,
            }
        ]
    )
    target = pd.DataFrame(
        [
            {
                "source": "mt5",
                "trade_id": "101",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "SELL",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-02 10:00:10"),
                "exit_time_utc": pd.Timestamp("2026-01-02 11:00:10"),
                "entry_price": 205.0,
                "exit_price": 185.0,
                "net_profit": 20.0,
            },
            {
                "source": "mt5",
                "trade_id": "102",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "SELL",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-02 10:00:15"),
                "exit_time_utc": pd.Timestamp("2026-01-02 11:00:15"),
                "entry_price": 200.02,
                "exit_price": 180.01,
                "net_profit": 19.0,
            },
        ]
    )

    matched, _, unmatched_target = match_trades(
        source,
        target,
        MatchConfig(matching_mode="hybrid", entry_price_tolerance=0.05, exit_price_tolerance=0.05),
    )

    assert len(matched) == 1
    assert str(matched.iloc[0]["mt5_trade_id"]) == "102"
    assert matched.iloc[0]["match_type"] == "hybrid_time_price"
    assert len(unmatched_target) == 1


def test_unmatched_trades_are_not_included_in_matched_points_totals():
    source = pd.DataFrame(
        [
            {
                "source": "ninjatrader",
                "trade_id": "1",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "BUY",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-02 10:00:00"),
                "exit_time_utc": pd.Timestamp("2026-01-02 11:00:00"),
                "entry_price": 100.0,
                "exit_price": 110.0,
                "net_profit": 10.0,
            },
            {
                "source": "ninjatrader",
                "trade_id": "2",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "BUY",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-05 10:00:00"),
                "exit_time_utc": pd.Timestamp("2026-01-05 11:00:00"),
                "entry_price": 150.0,
                "exit_price": 120.0,
                "net_profit": -30.0,
            },
        ]
    )
    target = pd.DataFrame(
        [
            {
                "source": "mt5",
                "trade_id": "101",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "BUY",
                "qty": 5.0,
                "entry_time_utc": pd.Timestamp("2026-01-03 15:00:00"),
                "exit_time_utc": pd.Timestamp("2026-01-03 16:00:00"),
                "entry_price": 100.0,
                "exit_price": 109.0,
                "net_profit": 45.0,
            }
        ]
    )

    matched, unmatched_source, unmatched_target = match_trades(
        source,
        target,
        MatchConfig(matching_mode="price", entry_price_tolerance=0.01, exit_price_tolerance=1.5),
    )

    assert len(matched) == 1
    assert len(unmatched_source) == 1
    assert unmatched_target.empty
    assert float(matched["nt_points"].sum()) == 10.0
    assert float(matched["mt5_points"].sum()) == 9.0


def test_hybrid_price_fallback_rejects_extreme_time_gap():
    source = pd.DataFrame(
        [
            {
                "source": "ninjatrader",
                "trade_id": "1",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "BUY",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-02 10:00:00"),
                "exit_time_utc": pd.Timestamp("2026-01-02 11:00:00"),
                "entry_price": 100.0,
                "exit_price": 110.0,
                "net_profit": 10.0,
            }
        ]
    )
    target = pd.DataFrame(
        [
            {
                "source": "mt5",
                "trade_id": "101",
                "symbol_raw": "BTCUSD",
                "symbol_norm": "BTCUSD",
                "side": "BUY",
                "qty": 1.0,
                "entry_time_utc": pd.Timestamp("2026-01-10 10:00:00"),
                "exit_time_utc": pd.Timestamp("2026-01-10 11:00:00"),
                "entry_price": 100.01,
                "exit_price": 110.01,
                "net_profit": 10.0,
            }
        ]
    )

    matched, unmatched_source, unmatched_target = match_trades(
        source,
        target,
        MatchConfig(matching_mode="hybrid", entry_price_tolerance=0.05, exit_price_tolerance=0.05),
    )

    assert matched.empty
    assert len(unmatched_source) == 1
    assert len(unmatched_target) == 1

