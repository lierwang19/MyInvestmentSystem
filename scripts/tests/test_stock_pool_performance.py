import sqlite3

from web.backend.api import stock_pool


def test_enrich_pool_rows_uses_single_market_query(monkeypatch):
    user_conn = sqlite3.connect(":memory:")
    user_conn.row_factory = sqlite3.Row
    user_conn.executescript(
        """
        CREATE TABLE stock_pool (
            ts_code TEXT,
            industry TEXT,
            score REAL,
            pb_score REAL,
            pe_score REAL
        );
        INSERT INTO stock_pool (ts_code, industry, score, pb_score, pe_score) VALUES
            ('000001.SZ', '银行', 88, 80, 90),
            ('600000.SH', '银行', 77, 70, 85);
        """
    )

    market_conn = sqlite3.connect(":memory:")
    market_conn.row_factory = sqlite3.Row
    market_conn.executescript(
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
        );
        INSERT INTO daily_price VALUES
            ('000001.SZ', '2026-04-15', 10, 10, 9, 10, 0, 0),
            ('000001.SZ', '2026-04-14', 9, 9, 8, 9, 0, 0),
            ('600000.SH', '2026-04-15', 20, 20, 18, 20, 0, 0),
            ('600000.SH', '2026-04-14', 19, 19, 17, 19, 0, 0);
        """
    )

    market_calls = {"count": 0}

    class _Ctx:
        def __init__(self, conn, counter=None):
            self.conn = conn
            self.counter = counter

        def __enter__(self):
            if self.counter is not None:
                self.counter["count"] += 1
            return self.conn

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(stock_pool, "get_db", lambda: _Ctx(user_conn))
    monkeypatch.setattr("web.backend.database.get_market_db", lambda: _Ctx(market_conn, market_calls))
    monkeypatch.setattr(
        stock_pool,
        "_load_scoring_params",
        lambda: (0.4, 0.4, 0.2, [{"max": 3, "score": 100}, {"else": 25}]),
    )

    rows = stock_pool.get_stock_pool()["data"]

    assert len(rows) == 2
    assert market_calls["count"] == 1
    row_map = {row["ts_code"]: row for row in rows}
    assert row_map["000001.SZ"]["latest"] == 10.0
    assert row_map["000001.SZ"]["pct_chg"] == round((10 - 9) / 9 * 100, 2)
    assert row_map["600000.SH"]["latest"] == 20.0
