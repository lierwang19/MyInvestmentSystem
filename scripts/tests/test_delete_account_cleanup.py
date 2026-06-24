# -*- coding: utf-8 -*-
"""
回归测试：delete_account_db() 清理 t_trading_items
===================================================
验证 accounts_db.py 的 delete_account_db() 在删除账户时，
同步清理 t_trading_items 表中的孤儿记录。

Bug 背景：原实现删除了 holdings/transactions/capital_flows/asset_history/
          archived_holdings，但遗漏了 t_trading_items，
          导致孤儿做 T 记录残留数据库。
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
        remarks TEXT
    )''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS asset_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        date TEXT NOT NULL,
        total_assets REAL DEFAULT 0.0
    )''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS archived_holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        stock_name TEXT
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
    CREATE TABLE IF NOT EXISTS strategy_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        account_id TEXT,
        run_date TEXT NOT NULL,
        status TEXT DEFAULT 'completed'
    )''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS strategy_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        strategy_id TEXT NOT NULL,
        account_id TEXT,
        run_date TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        signal TEXT NOT NULL
    )''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS strategy_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        strategy_id TEXT NOT NULL,
        account_id TEXT,
        run_date TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        action TEXT NOT NULL
    )''')
    conn.commit()


@pytest.fixture
def db_with_account():
    """准备有完整关联数据（含 t_trading_items）的账户"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    c = conn.cursor()

    c.execute("INSERT INTO accounts (id, name) VALUES ('acc1', '测试账户')")

    # 买入交易
    c.execute("""
        INSERT INTO transactions (account_id, ts_code, name, trans_type, price, amount, total_amt, date)
        VALUES ('acc1', '600000.SH', '浦发银行', 'BUY', 10.0, 1000, 10000.0, '2024-01-01')
    """)
    tx_id = c.lastrowid

    # 持仓
    c.execute("""
        INSERT INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost)
        VALUES ('acc1', '600000.SH', '浦发银行', 1000, 10.0, 10000.0)
    """)

    # 做 T 记录（此处为孤儿 bug 的来源）
    c.execute("""
        INSERT INTO t_trading_items (account_id, transaction_id, ts_code)
        VALUES ('acc1', ?, '600000.SH')
    """, (tx_id,))
    c.execute("""
        INSERT INTO t_trading_items (account_id, transaction_id, ts_code)
        VALUES ('acc1', ?, '600000.SH')
    """, (tx_id,))

    # 策略运行记录
    c.execute("""
        INSERT INTO strategy_runs (strategy_id, account_id, run_date)
        VALUES ('strategy-1', 'acc1', '2024-01-01')
    """)
    run_id = c.lastrowid
    c.execute("""
        INSERT INTO strategy_signals (run_id, strategy_id, account_id, run_date, ts_code, signal)
        VALUES (?, 'strategy-1', 'acc1', '2024-01-01', '600000.SH', 'BUY')
    """, (run_id,))
    c.execute("""
        INSERT INTO strategy_decisions (run_id, strategy_id, account_id, run_date, ts_code, action)
        VALUES (?, 'strategy-1', 'acc1', '2024-01-01', '600000.SH', 'BUY')
    """, (run_id,))

    conn.commit()
    yield conn
    conn.close()


def test_delete_account_cleans_t_trading_items(db_with_account):
    """删除账户后 t_trading_items 中不应有该账户的孤儿记录"""
    from web.backend.api.accounts_db import delete_account_db

    conn = db_with_account

    @contextmanager
    def _fake_get_db():
        yield conn

    # 删除前确认 t_trading_items 有记录
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM t_trading_items WHERE account_id='acc1'")
    before_count = c.fetchone()["cnt"]
    assert before_count == 2, "测试前提：应有 2 条 t_trading_items 记录"

    with patch('web.backend.api.accounts_db.get_db', _fake_get_db), \
         patch('web.backend.api.accounts_db.os.path.isdir', return_value=False):
        result = delete_account_db('acc1')

    assert result["status"] == "success"

    c.execute("SELECT COUNT(*) as cnt FROM t_trading_items WHERE account_id='acc1'")
    after_count = c.fetchone()["cnt"]
    assert after_count == 0, (
        f"删除账户后 t_trading_items 应清空，实际残留 {after_count} 条孤儿记录"
    )


def test_delete_account_cleans_all_tables(db_with_account):
    """删除账户时所有关联表都应清空（包含之前已验证的表）"""
    from web.backend.api.accounts_db import delete_account_db

    conn = db_with_account

    @contextmanager
    def _fake_get_db():
        yield conn

    with patch('web.backend.api.accounts_db.get_db', _fake_get_db), \
         patch('web.backend.api.accounts_db.os.path.isdir', return_value=False):
        delete_account_db('acc1')

    c = conn.cursor()
    tables = ['holdings', 'transactions', 't_trading_items']
    for table in tables:
        c.execute(f"SELECT COUNT(*) as cnt FROM {table} WHERE account_id='acc1'")
        count = c.fetchone()["cnt"]
        assert count == 0, f"删除账户后 {table} 应清空，实际残留 {count} 条"

    c.execute("SELECT COUNT(*) as cnt FROM accounts WHERE id='acc1'")
    assert c.fetchone()["cnt"] == 0, "accounts 表中账户记录应已删除"


def test_delete_account_cleans_strategy_tables(db_with_account):
    """删除账户后 strategy_runs/signals/decisions 中不应残留该账户数据"""
    from web.backend.api.accounts_db import delete_account_db

    conn = db_with_account

    @contextmanager
    def _fake_get_db():
        yield conn

    # 确认删除前三张表都有记录
    c = conn.cursor()
    for table in ['strategy_runs', 'strategy_signals', 'strategy_decisions']:
        c.execute(f"SELECT COUNT(*) as cnt FROM {table} WHERE account_id='acc1'")
        assert c.fetchone()["cnt"] == 1, f"测试前提：{table} 应有 1 条记录"

    with patch('web.backend.api.accounts_db.get_db', _fake_get_db), \
         patch('web.backend.api.accounts_db.os.path.isdir', return_value=False):
        delete_account_db('acc1')

    for table in ['strategy_runs', 'strategy_signals', 'strategy_decisions']:
        c.execute(f"SELECT COUNT(*) as cnt FROM {table} WHERE account_id='acc1'")
        count = c.fetchone()["cnt"]
        assert count == 0, (
            f"删除账户后 {table} 应无孤儿记录，实际残留 {count} 条"
        )


def test_delete_nonexistent_account_raises_404(db_with_account):
    """删除不存在的账户应返回 404"""
    from web.backend.api.accounts_db import delete_account_db
    from fastapi import HTTPException

    conn = db_with_account

    @contextmanager
    def _fake_get_db():
        yield conn

    with patch('web.backend.api.accounts_db.get_db', _fake_get_db):
        with pytest.raises(HTTPException) as exc_info:
            delete_account_db('nonexistent')

    assert exc_info.value.status_code == 404
