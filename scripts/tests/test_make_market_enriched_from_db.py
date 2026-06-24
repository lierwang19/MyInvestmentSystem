import sqlite3

from scripts.etl import make_market_enriched_from_db as enriched


def test_calendar_window_start_uses_open_interval():
    assert enriched.calendar_window_start("2026-04-16", years=1) == "2025-04-17"


def test_get_window_from_db_uses_calendar_days():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE daily_price (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO daily_price (ts_code, trade_date, open, high, low, close, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("000001.SZ", "2025-04-16", 10, 10, 5, 10, 0, 0),
            ("000001.SZ", "2025-04-17", 10, 10, 6, 10, 0, 0),
            ("000001.SZ", "2026-04-16", 10, 10, 7, 10, 0, 0),
        ],
    )

    start_date = enriched.calendar_window_start("2026-04-16", years=1)
    window_df = enriched.get_window_from_db(conn, "000001.SZ", start_date)

    assert start_date == "2025-04-17"
    assert window_df["trade_date"].tolist() == ["2025-04-17", "2026-04-16"]
    assert float(window_df["low"].min()) == 6.0
