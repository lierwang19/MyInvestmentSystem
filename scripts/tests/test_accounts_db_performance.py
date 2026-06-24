# -*- coding: utf-8 -*-
"""
回归测试：/api/accounts-db 不应对 market.db 做逐账户 N+1 查询。
"""

import os
import sqlite3
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _init_user_db(conn):
    c = conn.cursor()
    c.execute("""
        CREATE TABLE accounts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            broker_id INTEGER,
            remarks TEXT,
            cash_balance REAL DEFAULT 0.0,
            migrated INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE brokers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE capital_flows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            flow_type TEXT NOT NULL,
            amount REAL NOT NULL,
            date TEXT NOT NULL,
            remarks TEXT,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            name TEXT,
            quantity INTEGER NOT NULL DEFAULT 0,
            cost_price REAL NOT NULL DEFAULT 0.0,
            total_cost REAL NOT NULL DEFAULT 0.0,
            buy_pb REAL DEFAULT 0.0,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(account_id, ts_code)
        )
    """)
    conn.commit()


def _init_market_db(conn):
    c = conn.cursor()
    c.execute("""
        CREATE TABLE daily_price (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            close REAL,
            low REAL
        )
    """)
    conn.commit()


def test_list_accounts_db_batches_market_value_lookup(monkeypatch):
    from web.backend.api.accounts_db import list_accounts_db

    user_conn = sqlite3.connect(":memory:")
    user_conn.row_factory = sqlite3.Row
    _init_user_db(user_conn)

    market_conn = sqlite3.connect(":memory:")
    market_conn.row_factory = sqlite3.Row
    _init_market_db(market_conn)

    uc = user_conn.cursor()
    uc.execute("INSERT INTO accounts (id, name, cash_balance, created_at) VALUES ('acc1', 'A-01', 1000, '2026-04-15')")
    uc.execute("INSERT INTO accounts (id, name, cash_balance, created_at) VALUES ('acc2', 'A-02', 2000, '2026-04-15')")
    uc.execute("INSERT INTO capital_flows (account_id, flow_type, amount, date) VALUES ('acc1', 'DEPOSIT', 10000, '2026-04-15')")
    uc.execute("INSERT INTO capital_flows (account_id, flow_type, amount, date) VALUES ('acc2', 'DEPOSIT', 20000, '2026-04-15')")
    uc.execute("INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost) VALUES ('acc1', '600000.SH', '浦发银行', 100, 10, 1000)")
    uc.execute("INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost) VALUES ('acc1', '601318.SH', '中国平安', 50, 45, 2250)")
    uc.execute("INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost) VALUES ('acc2', '600000.SH', '浦发银行', 200, 10, 2000)")
    user_conn.commit()

    mc = market_conn.cursor()
    mc.execute("INSERT INTO daily_price (ts_code, trade_date, close, low) VALUES ('600000.SH', '2026-04-15', 10.5, 10.0)")
    mc.execute("INSERT INTO daily_price (ts_code, trade_date, close, low) VALUES ('601318.SH', '2026-04-15', 59.0, 58.0)")
    market_conn.commit()

    market_calls = {"count": 0}

    @contextmanager
    def _fake_get_db():
        yield user_conn

    @contextmanager
    def _fake_get_market_db():
        market_calls["count"] += 1
        yield market_conn

    monkeypatch.setattr("web.backend.api.accounts_db.get_db", _fake_get_db)
    monkeypatch.setattr("web.backend.api.accounts_db.get_market_db", _fake_get_market_db)

    result = list_accounts_db()

    assert result["status"] == "success"
    assert market_calls["count"] == 1

    data = {row["id"]: row for row in result["data"]}
    assert data["acc1"]["holdings_value"] == 100 * 10.5 + 50 * 59.0
    assert data["acc2"]["holdings_value"] == 200 * 10.5
    assert data["acc1"]["total_assets"] == data["acc1"]["holdings_value"] + 1000
    assert data["acc2"]["total_assets"] == data["acc2"]["holdings_value"] + 2000

    user_conn.close()
    market_conn.close()
