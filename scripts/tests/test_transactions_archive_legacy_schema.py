# -*- coding: utf-8 -*-
"""
兼容性回归测试：旧版 user_data.db 的 archived_holdings 可能没有
UNIQUE(account_id, ts_code) 约束，手动归档仍应成功。
"""

import os
import sqlite3
import sys
from contextlib import contextmanager

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _init_test_db(conn):
    c = conn.cursor()

    c.execute('''
    CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        broker_id INTEGER,
        remarks TEXT,
        cash_balance REAL DEFAULT 0.0,
        migrated INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        name TEXT,
        quantity INTEGER NOT NULL DEFAULT 0,
        cost_price REAL NOT NULL DEFAULT 0.0,
        total_cost REAL NOT NULL DEFAULT 0.0,
        buy_pb REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_id, ts_code)
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS archived_holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        stock_name TEXT,
        total_buy_amount REAL DEFAULT 0,
        total_sell_amount REAL DEFAULT 0,
        total_dividend REAL DEFAULT 0,
        total_profit REAL DEFAULT 0,
        profit_pct REAL DEFAULT 0,
        total_fees REAL DEFAULT 0,
        first_buy_date TEXT,
        last_sell_date TEXT,
        archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()


@pytest.fixture
def mock_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_test_db(conn)

    @contextmanager
    def _get_db():
        yield conn

    monkeypatch.setattr("web.backend.api.transactions_archive.get_db", _get_db)
    yield conn
    conn.close()


def test_archive_holding_works_without_unique_constraint(mock_db, monkeypatch):
    from web.backend.api.transactions_archive import archive_holding, ArchiveRequest

    mock_db.execute(
        "INSERT INTO accounts (id, name, cash_balance) VALUES (?, ?, ?)",
        ("acc1", "TestAccount", 10000.0),
    )
    mock_db.execute(
        """
        INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost, buy_pb)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("acc1", "600000.SH", "浦发银行", 0, 0.0, 0.0, 0.95),
    )
    mock_db.commit()

    monkeypatch.setattr(
        "web.backend.api.transactions.get_stock_summary",
        lambda account_id, ts_code: {
            "status": "success",
            "data": {
                "current_quantity": 0,
                "total_buy_amount": 1005.02,
                "total_sell_amount": 1193.78,
                "total_dividend": 0.0,
                "total_fees": 11.24,
                "realized_pnl": 188.76,
                "first_buy_date": "2026-04-14",
                "last_sell_date": "2026-04-15",
            },
        },
    )

    result = archive_holding("acc1", ArchiveRequest(ts_code="600000.SH", stock_name="浦发银行"))

    assert result["status"] == "success"
    assert result["profit"] == 188.76
    row = mock_db.execute(
        """
        SELECT ts_code, total_buy_amount, total_sell_amount, total_profit, profit_pct, total_fees
        FROM archived_holdings
        WHERE account_id = ? AND ts_code = ?
        """,
        ("acc1", "600000.SH"),
    ).fetchone()
    assert row is not None
    assert row["total_buy_amount"] == 1005.02
    assert row["total_sell_amount"] == 1193.78
    assert row["total_profit"] == 188.76
    assert row["profit_pct"] == 18.78
    assert row["total_fees"] == 11.24

    holding = mock_db.execute(
        "SELECT * FROM holdings WHERE account_id = ? AND ts_code = ?",
        ("acc1", "600000.SH"),
    ).fetchone()
    assert holding is None
