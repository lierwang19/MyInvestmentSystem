# -*- coding: utf-8 -*-
"""
回归测试：_rebuild_holdings() 保留 buy_pb
==========================================
验证 import_flow.py 的 _rebuild_holdings() 在重建持仓后，
原有的 buy_pb 值不会被重置为 0。

Bug 背景：原实现清空 holdings 后 INSERT 没有写 buy_pb 列，
          导致 buy_pb 默认为 0.0，破坏 from_buy_pb 卖出分桶策略。
"""

import sys
import os
import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest


def _init_db(conn):
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        broker_id INTEGER,
        cash_balance REAL DEFAULT 0.0,
        migrated INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
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
    )''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        name TEXT,
        trans_type TEXT NOT NULL,
        price REAL NOT NULL,
        amount INTEGER NOT NULL,
        total_amt REAL NOT NULL,
        fee REAL DEFAULT 0.0,
        stamp_tax REAL DEFAULT 0.0,
        date TEXT NOT NULL,
        remarks TEXT,
        is_special INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
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
    )''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS capital_flows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        flow_type TEXT NOT NULL,
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        remarks TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()


@pytest.fixture
def db_with_holding():
    """准备一个已有 buy_pb=1.23 持仓的内存数据库"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    c = conn.cursor()

    # 插入账户
    c.execute("INSERT INTO accounts (id, name, cash_balance) VALUES ('acc1', '测试账户', 10000)")

    # 插入一笔买入交易
    c.execute("""
        INSERT INTO transactions (account_id, ts_code, name, trans_type, price, amount, total_amt, fee, date)
        VALUES ('acc1', '600000.SH', '浦发银行', 'BUY', 10.0, 1000, 10000.0, 5.0, '2024-01-01')
    """)

    # 插入持仓，buy_pb 设为非零值 1.23
    c.execute("""
        INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost, buy_pb)
        VALUES ('acc1', '600000.SH', '浦发银行', 1000, 10.005, 10005.0, 1.23)
    """)
    conn.commit()
    yield conn
    conn.close()


def test_rebuild_holdings_preserves_buy_pb(db_with_holding):
    """_rebuild_holdings() 重建后 buy_pb 应保持原值 1.23，不能被重置为 0"""
    from web.backend.api.import_flow import _rebuild_holdings

    conn = db_with_holding

    @contextmanager
    def _fake_get_db():
        yield conn

    with patch('web.backend.api.import_flow.get_db', _fake_get_db):
        _rebuild_holdings('acc1', conn)

    c = conn.cursor()
    c.execute("SELECT buy_pb FROM holdings WHERE account_id='acc1' AND ts_code='600000.SH'")
    row = c.fetchone()

    assert row is not None, "重建后 holdings 不应为空"
    assert row["buy_pb"] == pytest.approx(1.23, abs=0.001), (
        f"buy_pb 应保留为 1.23，实际得到 {row['buy_pb']} — 说明 _rebuild_holdings 丢失了 buy_pb"
    )


def test_rebuild_holdings_new_stock_buy_pb_defaults_zero(db_with_holding):
    """_rebuild_holdings() 对新增股票（无旧 buy_pb 记录）应默认为 0.0"""
    conn = db_with_holding
    c = conn.cursor()

    # 插入第二只股票的买入交易，但 holdings 表里没有对应记录（新股）
    c.execute("""
        INSERT INTO transactions (account_id, ts_code, name, trans_type, price, amount, total_amt, fee, date)
        VALUES ('acc1', '000001.SZ', '平安银行', 'BUY', 12.0, 500, 6000.0, 5.0, '2024-02-01')
    """)
    conn.commit()

    from web.backend.api.import_flow import _rebuild_holdings

    @contextmanager
    def _fake_get_db():
        yield conn

    with patch('web.backend.api.import_flow.get_db', _fake_get_db):
        _rebuild_holdings('acc1', conn)

    c.execute("SELECT buy_pb FROM holdings WHERE account_id='acc1' AND ts_code='000001.SZ'")
    row = c.fetchone()

    assert row is not None, "新股应该出现在重建后的 holdings 中"
    assert row["buy_pb"] == pytest.approx(0.0, abs=0.001), (
        f"新股 buy_pb 应默认为 0.0，实际得到 {row['buy_pb']}"
    )


def test_rebuild_holdings_counts_rights_fee_into_cost_and_cash(db_with_holding):
    """_rebuild_holdings() 应将配股手续费计入成本，并同步扣减现金"""
    conn = db_with_holding
    c = conn.cursor()

    c.execute("""
        INSERT INTO transactions (account_id, ts_code, name, trans_type, price, amount, total_amt, fee, stamp_tax, date)
        VALUES ('acc1', '600000.SH', '浦发银行', 'RIGHTS', 8.0, 100, 800.0, 2.0, 0.0, '2024-02-01')
    """)
    conn.commit()

    from web.backend.api.import_flow import _rebuild_holdings

    @contextmanager
    def _fake_get_db():
        yield conn

    with patch('web.backend.api.import_flow.get_db', _fake_get_db):
        _rebuild_holdings('acc1', conn)

    c.execute("SELECT quantity, total_cost, cost_price FROM holdings WHERE account_id='acc1' AND ts_code='600000.SH'")
    row = c.fetchone()
    assert row is not None
    assert row["quantity"] == 1100
    assert row["total_cost"] == pytest.approx(10807.0, abs=0.01)
    assert row["cost_price"] == pytest.approx(round(10807.0 / 1100, 4), abs=0.001)

    c.execute("SELECT cash_balance FROM accounts WHERE id='acc1'")
    cash = c.fetchone()["cash_balance"]
    assert cash == pytest.approx(-10807.0, abs=0.01)
