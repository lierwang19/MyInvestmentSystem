# -*- coding: utf-8 -*-
"""
回归测试：delete_holding() 回滚 cash_balance
=============================================
验证 transactions.py 的 delete_holding() 在删除持仓后，
账户的 cash_balance 会正确回滚，不会残留错误余额。

Bug 背景：原实现删除 holdings + transactions + capital_flows 三张表，
          但未更新 accounts.cash_balance，导致余额永久偏高（已卖出金额残留）。
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
    CREATE TABLE IF NOT EXISTS capital_flows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        flow_type TEXT NOT NULL,
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        remarks TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS t_trading_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        transaction_id INTEGER,
        ts_code TEXT,
        removed_at TEXT DEFAULT NULL
    )''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS archived_holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        stock_name TEXT,
        total_buy_amount REAL DEFAULT 0,
        total_sell_amount REAL DEFAULT 0,
        total_profit REAL DEFAULT 0,
        first_buy_date TEXT,
        last_sell_date TEXT,
        archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()


@pytest.fixture
def db_with_buy_sell():
    """
    账户初始现金 0，买入 10000 + 手续费 5，后卖出 11000 - 手续费 5 - 印花税 11。
    当前 cash_balance = 0 - 10005 + 10984 = 979 元。
    持仓中还有 500 股未卖出，cost=5000，现金已减少 5005。
    删除该持仓后，cash_balance 应回到 0。
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    c = conn.cursor()

    # 账户：现金余额 = 买入后卖出一半后的真实余额
    # 买入 1000 股 @10, fee=5  → 现金 -10005
    # 卖出 500 股  @11, fee=5, stamp=5 → 现金 +5490
    # 当前持仓 500 股，cash = 0 - 10005 + 5490 = -4515
    # 这是正常情况：买入占用了现金，持仓就是资产
    c.execute("INSERT INTO accounts (id, name, cash_balance) VALUES ('acc1', '测试账户', -4515.0)")

    # 买入 1000 股
    c.execute("""
        INSERT INTO transactions (account_id, ts_code, name, trans_type, price, amount, total_amt, fee, stamp_tax, date)
        VALUES ('acc1', '600000.SH', '浦发银行', 'BUY', 10.0, 1000, 10000.0, 5.0, 0.0, '2024-01-01')
    """)
    # 卖出 500 股
    c.execute("""
        INSERT INTO transactions (account_id, ts_code, name, trans_type, price, amount, total_amt, fee, stamp_tax, date)
        VALUES ('acc1', '600000.SH', '浦发银行', 'SELL', 11.0, 500, 5500.0, 5.0, 5.0, '2024-06-01')
    """)

    # 持仓（剩余 500 股）
    c.execute("""
        INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost)
        VALUES ('acc1', '600000.SH', '浦发银行', 500, 10.005, 5002.5)
    """)

    # 做 T 记录（应随持仓一起删除）
    c.execute("""
        INSERT INTO t_trading_items (account_id, ts_code, transaction_id)
        VALUES ('acc1', '600000.SH', 1)
    """)

    # 归档记录（模拟之前部分清仓留下的归档，应随持仓一起删除）
    c.execute("""
        INSERT INTO archived_holdings (account_id, ts_code, stock_name)
        VALUES ('acc1', '600000.SH', '浦发银行')
    """)
    conn.commit()
    yield conn
    conn.close()


def test_delete_holding_rollbacks_cash(db_with_buy_sell):
    """
    删除持仓后 cash_balance 应正确回滚：
    原 cash = -4515（买入10005，卖出5490），删除后 cash = 0（所有交易撤销）
    """
    from web.backend.api.transactions import delete_holding

    conn = db_with_buy_sell

    @contextmanager
    def _fake_get_db():
        yield conn

    with patch('web.backend.api.transactions.get_db', _fake_get_db):
        result = delete_holding('acc1', '600000.SH')

    assert result["status"] == "success"

    c = conn.cursor()
    c.execute("SELECT cash_balance FROM accounts WHERE id='acc1'")
    row = c.fetchone()

    # 删除了买入(-10005)和卖出(+5490)的记录后，cash 从 -4515 回滚
    # cash_delta = BUY 影响 -(10000+5) + SELL 影响 +(5500-5-5) = -10005 + 5490 = -4515
    # new_cash = old_cash - cash_delta = -4515 - (-4515) = 0
    assert row["cash_balance"] == pytest.approx(0.0, abs=0.01), (
        f"删除持仓后 cash_balance 应回到 0，实际为 {row['cash_balance']}"
    )


def test_delete_holding_not_found_raises_404(db_with_buy_sell):
    """删除不存在的持仓应返回 404"""
    from web.backend.api.transactions import delete_holding
    from fastapi import HTTPException

    conn = db_with_buy_sell

    @contextmanager
    def _fake_get_db():
        yield conn

    with patch('web.backend.api.transactions.get_db', _fake_get_db):
        with pytest.raises(HTTPException) as exc_info:
            delete_holding('acc1', '999999.SH')

    assert exc_info.value.status_code == 404


def test_delete_holding_no_transactions_cash_unchanged(db_with_buy_sell):
    """如果持仓对应的交易全部为 MERGE 类型（cash_delta=0），cash_balance 不变"""
    conn = db_with_buy_sell
    c = conn.cursor()

    # 额外插入一只持仓，没有 BUY/SELL 交易（模拟纯 MERGE_IN 到账）
    c.execute("""
        INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost)
        VALUES ('acc1', '688001.SH', '新股', 100, 0.0, 0.0)
    """)
    # 对应交易类型不影响现金（无 BUY/SELL/DIVIDEND）
    conn.commit()

    original_cash = -4515.0

    from web.backend.api.transactions import delete_holding

    @contextmanager
    def _fake_get_db():
        yield conn

    with patch('web.backend.api.transactions.get_db', _fake_get_db):
        delete_holding('acc1', '688001.SH')

    c.execute("SELECT cash_balance FROM accounts WHERE id='acc1'")
    row = c.fetchone()

    assert row["cash_balance"] == pytest.approx(original_cash, abs=0.01), (
        f"无现金交易的持仓被删除后，cash_balance 不应变化"
    )


def test_delete_holding_cleans_t_trading_and_archived(db_with_buy_sell):
    """删除持仓后，t_trading_items 和 archived_holdings 中同股票的记录也应清除"""
    from web.backend.api.transactions import delete_holding

    conn = db_with_buy_sell

    @contextmanager
    def _fake_get_db():
        yield conn

    # 确认删除前有记录
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM t_trading_items WHERE account_id='acc1' AND ts_code='600000.SH'")
    assert c.fetchone()["cnt"] == 1, "测试前提：应有 1 条 t_trading_items 记录"
    c.execute("SELECT COUNT(*) as cnt FROM archived_holdings WHERE account_id='acc1' AND ts_code='600000.SH'")
    assert c.fetchone()["cnt"] == 1, "测试前提：应有 1 条 archived_holdings 记录"

    with patch('web.backend.api.transactions.get_db', _fake_get_db):
        delete_holding('acc1', '600000.SH')

    c.execute("SELECT COUNT(*) as cnt FROM t_trading_items WHERE account_id='acc1' AND ts_code='600000.SH'")
    assert c.fetchone()["cnt"] == 0, "删除持仓后 t_trading_items 孤儿记录应清空"

    c.execute("SELECT COUNT(*) as cnt FROM archived_holdings WHERE account_id='acc1' AND ts_code='600000.SH'")
    assert c.fetchone()["cnt"] == 0, "删除持仓后 archived_holdings 残影应清空"
