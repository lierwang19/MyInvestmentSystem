import sqlite3

import pandas as pd

from web.backend.api import analysis, stock_pool


class _Ctx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


def test_stock_pool_response_cache_reuses_enriched_payload(monkeypatch):
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
        CREATE TABLE risk_flags (
            ts_code TEXT
        );
        INSERT INTO stock_pool (ts_code, industry, score, pb_score, pe_score) VALUES
            ('000001.SZ', '银行', 88, 80, 90);
        """
    )

    stock_pool._STOCK_POOL_RESPONSE_CACHE["key"] = None
    stock_pool._STOCK_POOL_RESPONSE_CACHE["value"] = None

    calls = {"count": 0}

    def fake_enrich(rows):
        calls["count"] += 1
        return [dict(rows[0]) | {"latest": 10.0}]

    monkeypatch.setattr(stock_pool, "get_db", lambda: _Ctx(user_conn))
    monkeypatch.setattr(stock_pool, "_get_stock_pool_cache_key", lambda: ("stable",))
    monkeypatch.setattr(stock_pool, "_enrich_pool_rows", fake_enrich)

    first = stock_pool.get_stock_pool()
    second = stock_pool.get_stock_pool()

    assert first == second
    assert calls["count"] == 1


def test_analyze_holdings_response_cache_reuses_market_enrichment(monkeypatch):
    user_conn = sqlite3.connect(":memory:")
    user_conn.row_factory = sqlite3.Row
    user_conn.executescript(
        """
        CREATE TABLE accounts (
            id TEXT PRIMARY KEY,
            cash_balance REAL
        );
        CREATE TABLE holdings (
            account_id TEXT,
            ts_code TEXT,
            name TEXT,
            quantity REAL,
            cost_price REAL,
            total_cost REAL
        );
        INSERT INTO accounts (id, cash_balance) VALUES ('A-02', 1000);
        INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost) VALUES
            ('A-02', '000001.SZ', '平安银行', 100, 10, 1000);
        """
    )

    analysis._HOLDINGS_RESPONSE_CACHE.clear()

    calls = {"count": 0}

    def fake_market_loader(ts_codes):
        calls["count"] += 1
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "tdx_l1": "金融",
                    "tdx_l2": "银行",
                    "anchor_industry": "银行",
                    "pb": 1.2,
                    "pe_ttm": 5.6,
                    "pb_source": "daily_valuation",
                    "latest": 11.0,
                    "low_252d": 8.0,
                    "rise_from_1y_low_pct": 37.5,
                    "low_756d": 7.0,
                    "rise_from_3y_low_pct": 57.14,
                    "low_all": 6.0,
                    "rise_from_all_low_pct": 83.33,
                }
            ]
        )

    monkeypatch.setattr("web.backend.database.get_db", lambda: _Ctx(user_conn))
    monkeypatch.setattr(analysis, "_get_holdings_cache_key", lambda account_id: (account_id, "stable"))
    monkeypatch.setattr(analysis, "_load_market_data_from_db", fake_market_loader)
    monkeypatch.setattr(analysis, "_get_scoring_config", lambda: {"positives": {}})
    monkeypatch.setattr(analysis.build_portfolio, "get_anchor", lambda industry, cfg: 2.5)
    monkeypatch.setattr(analysis.build_portfolio, "calc_win_prob", lambda pb, anchor: 0.6)
    monkeypatch.setattr(analysis.build_portfolio, "calc_allocation_ratio", lambda win_prob: 0.5)

    first = analysis.analyze_holdings("A-02")
    second = analysis.analyze_holdings("A-02")

    assert first == second
    assert calls["count"] == 1
    assert first["holdings"][0]["price"] == 11.0
