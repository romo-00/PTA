from __future__ import annotations

from io import BytesIO, StringIO

import pandas as pd

from importers.mt5_html import load_mt5_html
from importers.mt5_xlsx import load_mt5_xlsx
from importers.ninjatrader_csv import load_ninjatrader_csv
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


def test_sequential_matching_with_named_differences():
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
                "entry_time_utc": pd.Timestamp("2026-01-02 10:01:00"),
                "exit_time_utc": pd.Timestamp("2026-01-02 10:31:00"),
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
    assert matched.iloc[0]["model_to_live_entry_difference_pts"] == 1.0
    assert matched.iloc[0]["model_to_live_exit_difference_pts"] == 1.0
    assert matched.iloc[0]["nt_points"] == 20.0
    assert matched.iloc[0]["mt5_points"] == 20.0
    assert matched.iloc[0]["points_delta"] == 0.0
    assert matched.iloc[0]["match_type"] == "sequential"
