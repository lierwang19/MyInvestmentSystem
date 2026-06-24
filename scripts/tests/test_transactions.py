# -*- coding: utf-8 -*-
"""
交易计算单元测试 — transactions.py 核心逻辑
=============================================
覆盖场景：
  1. 买入手续费 = max(成交额×佣金率, 最低佣金)
  2. 买入手续费 — 大额时按比例
  3. 卖出手续费 + 印花税
  4. 买入后余额变动
  5. 卖出后余额变动
  6. 分红后成本价更新
  7. 分红后现金增加
  8. 送股(SPLIT)摊薄
  9. 已有持仓再买入（加仓加权平均）
 10. 无 broker 使用默认费率

测试方式: mock get_db() 返回 in-memory SQLite，初始化必要表结构。
"""

import sys
import os
import sqlite3
from contextlib import contextmanager

# 确保能 import web.backend 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest


# ─────────────────────────────────────────────────────────────
# In-memory DB 初始化 + mock fixture
# ─────────────────────────────────────────────────────────────

def _init_test_db(conn):
    """在内存 DB 中创建测试所需的最小表结构"""
    c = conn.cursor()

    c.execute('''
    CREATE TABLE IF NOT EXISTS brokers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        commission_rate REAL DEFAULT 0.0003,
        min_commission REAL DEFAULT 5.0,
        stamp_tax_rate REAL DEFAULT 0.001,
        transfer_fee_rate REAL DEFAULT 0.00002,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        broker_id INTEGER,
        remarks TEXT,
        cash_balance REAL DEFAULT 0.0,
        migrated INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (broker_id) REFERENCES brokers(id)
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (account_id) REFERENCES accounts(id)
    )
    ''')

    conn.commit()


@pytest.fixture
def mock_db(monkeypatch):
    """提供 in-memory SQLite 并 mock get_db()"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_test_db(conn)

    @contextmanager
    def _get_db():
        yield conn

    monkeypatch.setattr("web.backend.api.transactions.get_db", _get_db)
    yield conn
    conn.close()


def _setup_account(conn, account_id="acc1", cash=100_000, broker_id=None):
    """创建测试账户"""
    conn.execute("INSERT OR REPLACE INTO accounts (id, name, broker_id, cash_balance) VALUES (?, ?, ?, ?)",
                 (account_id, "TestAccount", broker_id, cash))
    conn.commit()


def _setup_broker(conn, broker_id=1, commission_rate=0.0003,
                  min_commission=5.0, stamp_tax_rate=0.001,
                  transfer_fee_rate=0.00002):
    """创建券商"""
    conn.execute(
        "INSERT OR REPLACE INTO brokers (id, name, commission_rate, min_commission, stamp_tax_rate, transfer_fee_rate) VALUES (?, ?, ?, ?, ?, ?)",
        (broker_id, f"Broker{broker_id}", commission_rate, min_commission, stamp_tax_rate, transfer_fee_rate))
    conn.commit()


def _setup_holding(conn, account_id="acc1", ts_code="000001.SZ", name="平安",
                   quantity=1000, cost_price=10.0, total_cost=10000.0):
    """创建持仓"""
    conn.execute(
        "INSERT OR REPLACE INTO holdings (account_id, ts_code, name, quantity, cost_price, total_cost) VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, ts_code, name, quantity, cost_price, total_cost))
    conn.commit()


def _get_cash(conn, account_id="acc1"):
    """查询账户余额"""
    row = conn.execute("SELECT cash_balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return row["cash_balance"] if row else 0


def _get_holding(conn, account_id="acc1", ts_code="000001.SZ"):
    """查询持仓"""
    return conn.execute(
        "SELECT * FROM holdings WHERE account_id = ? AND ts_code = ?",
        (account_id, ts_code)).fetchone()


# ─────────────────────────────────────────────────────────────
# 1. 买入手续费 — 最低佣金生效
# ─────────────────────────────────────────────────────────────

def test_buy_fee_min_commission(mock_db):
    """小额买入: 佣金触发最低 5 元，再叠加过户费 0.2 元 → fee = 5.2"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    _setup_account(mock_db, cash=200_000)

    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="BUY",
        price=10.0, amount=1000, date="2025-01-15"
    )
    result = create_transaction("acc1", trans)

    assert result["status"] == "success"
    assert result["fee"] == 5.2, "小额买入应使用最低佣金并叠加过户费"


# ─────────────────────────────────────────────────────────────
# 2. 买入手续费 — 大额按比例
# ─────────────────────────────────────────────────────────────

def test_buy_fee_by_rate(mock_db):
    """大额买入: 比例佣金 30.0 元，再叠加过户费 2.0 元 → fee = 32.0"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    _setup_account(mock_db, cash=500_000)

    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="BUY",
        price=100.0, amount=1000, date="2025-01-15"
    )
    result = create_transaction("acc1", trans)

    assert result["fee"] == 32.0


# ─────────────────────────────────────────────────────────────
# 3. 卖出手续费 + 印花税
# ─────────────────────────────────────────────────────────────

def test_sell_fee_and_stamp_tax(mock_db):
    """卖出: fee + stamp_tax (0.1%)"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    _setup_account(mock_db, cash=50_000)
    _setup_holding(mock_db, quantity=1000, cost_price=10.0, total_cost=10000.0)

    # 卖出 500 股 @ 20元 → total_amt = 10000
    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="SELL",
        price=20.0, amount=500, date="2025-01-20"
    )
    result = create_transaction("acc1", trans)

    total_amt = 20.0 * 500  # 10000
    expected_fee = max(total_amt * 0.0003, 5.0) + total_amt * 0.00002  # 5.2
    expected_stamp_tax = total_amt * 0.001  # 10.0

    assert result["fee"] == expected_fee
    assert result["stamp_tax"] == expected_stamp_tax


def test_transfer_fee_applied_to_buy_and_sell(mock_db):
    """买卖均应计入过户费。"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    _setup_broker(mock_db, broker_id=1, transfer_fee_rate=0.00002)
    _setup_account(mock_db, cash=200_000, broker_id=1)

    buy = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="BUY",
        price=10.0, amount=1000, date="2025-01-15"
    )
    buy_result = create_transaction("acc1", buy)
    assert buy_result["fee"] == 5.2

    _setup_holding(mock_db, quantity=1000, cost_price=10.0, total_cost=10000.0)
    sell = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="SELL",
        price=20.0, amount=500, date="2025-01-20"
    )
    sell_result = create_transaction("acc1", sell)
    assert sell_result["fee"] == 5.2


def test_hk_trade_no_transfer_fee(mock_db):
    """港股买卖不应计入 A 股过户费。"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    _setup_broker(mock_db, broker_id=1, transfer_fee_rate=0.00002)
    _setup_account(mock_db, cash=200_000, broker_id=1)

    buy = TransactionCreate(
        ts_code="00700.HK", name="腾讯控股", trans_type="BUY",
        price=10.0, amount=1000, date="2025-01-15"
    )
    buy_result = create_transaction("acc1", buy)
    assert buy_result["fee"] == 5.0


# ─────────────────────────────────────────────────────────────
# 4. 买入后余额变动
# ─────────────────────────────────────────────────────────────

def test_buy_cash_deduction(mock_db):
    """买入后: cash = 原余额 - total_amt - fee"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    initial_cash = 200_000.0
    _setup_account(mock_db, cash=initial_cash)

    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="BUY",
        price=10.0, amount=1000, date="2025-01-15"
    )
    result = create_transaction("acc1", trans)

    total_amt = 10.0 * 1000  # 10000
    fee = result["fee"]
    expected_cash = initial_cash - total_amt - fee
    actual_cash = _get_cash(mock_db)

    assert abs(actual_cash - expected_cash) < 0.01, \
        f"买入后余额应为 {expected_cash}, 实际 {actual_cash}"


# ─────────────────────────────────────────────────────────────
# 5. 卖出后余额变动
# ─────────────────────────────────────────────────────────────

def test_sell_cash_addition(mock_db):
    """卖出后: cash = 原余额 + total_amt - fee - stamp_tax"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    initial_cash = 50_000.0
    _setup_account(mock_db, cash=initial_cash)
    _setup_holding(mock_db, quantity=1000, cost_price=10.0, total_cost=10000.0)

    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="SELL",
        price=20.0, amount=500, date="2025-01-20"
    )
    result = create_transaction("acc1", trans)

    total_amt = 20.0 * 500
    fee = result["fee"]
    stamp_tax = result["stamp_tax"]
    expected_cash = initial_cash + total_amt - fee - stamp_tax
    actual_cash = _get_cash(mock_db)

    assert abs(actual_cash - expected_cash) < 0.01, \
        f"卖出后余额应为 {expected_cash}, 实际 {actual_cash}"


# ─────────────────────────────────────────────────────────────
# 6. 分红后成本价更新
# ─────────────────────────────────────────────────────────────

def test_dividend_updates_cost_price(mock_db):
    """分红: total_cost -= dividend_amount, cost_price 重算"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    _setup_account(mock_db, cash=50_000)
    _setup_holding(mock_db, quantity=1000, cost_price=10.0, total_cost=10000.0)

    # 分红 0.5元/股 × 1000股 = 500元
    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="DIVIDEND",
        price=0.5, amount=1000, date="2025-06-15"
    )
    result = create_transaction("acc1", trans)
    assert result["status"] == "success"

    holding = _get_holding(mock_db)
    expected_total_cost = 10000.0 - 500.0  # 9500
    expected_cost_price = expected_total_cost / 1000  # 9.5

    assert abs(holding["total_cost"] - expected_total_cost) < 0.01
    assert abs(holding["cost_price"] - expected_cost_price) < 0.01


# ─────────────────────────────────────────────────────────────
# 7. 分红后现金增加
# ─────────────────────────────────────────────────────────────

def test_dividend_increases_cash(mock_db):
    """分红: cash += dividend_amount（只增加一次）"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    initial_cash = 50_000.0
    _setup_account(mock_db, cash=initial_cash)
    _setup_holding(mock_db, quantity=1000, cost_price=10.0, total_cost=10000.0)

    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="DIVIDEND",
        price=0.5, amount=1000, date="2025-06-15"
    )
    create_transaction("acc1", trans)

    actual_cash = _get_cash(mock_db)
    expected_cash = initial_cash + 500.0  # 期望值: 只加一次
    assert abs(actual_cash - expected_cash) < 0.01


# ─────────────────────────────────────────────────────────────
# 8. 送股(SPLIT)摊薄
# ─────────────────────────────────────────────────────────────

def test_split_dilutes_cost_price(mock_db):
    """送股: 数量增加, total_cost不变, cost_price下降"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    _setup_account(mock_db, cash=50_000)
    _setup_holding(mock_db, quantity=1000, cost_price=10.0, total_cost=10000.0)

    # 送 500 股（10送5）
    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="SPLIT",
        price=0.0, amount=500, date="2025-07-01"
    )
    result = create_transaction("acc1", trans)
    assert result["status"] == "success"

    holding = _get_holding(mock_db)
    assert holding["quantity"] == 1500, "送股后总量 = 1000 + 500"
    # total_cost 不变，cost_price = 10000 / 1500 ≈ 6.6667
    expected_cost_price = 10000.0 / 1500
    assert abs(holding["cost_price"] - round(expected_cost_price, 4)) < 0.001


def test_rights_fee_updates_cash_and_cost(mock_db):
    """配股手续费应同时进入 cash_balance 与持仓总成本"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    _setup_account(mock_db, cash=20_000)
    _setup_holding(mock_db, quantity=1000, cost_price=10.0, total_cost=10000.0)

    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="RIGHTS",
        price=8.0, amount=100, date="2025-07-02", fee=2.0
    )
    result = create_transaction("acc1", trans)

    assert result["cash_change"] == -802.0

    holding = _get_holding(mock_db)
    assert holding["quantity"] == 1100
    assert abs(holding["total_cost"] - 10802.0) < 0.01
    assert abs(holding["cost_price"] - round(10802.0 / 1100, 4)) < 0.001
    assert abs(_get_cash(mock_db) - 19198.0) < 0.01


def test_stock_summary_includes_split_and_rights(mock_db):
    """股票摘要应把送股/配股后的数量和成本纳入口径"""
    from web.backend.api.transactions import create_transaction, get_stock_summary, TransactionCreate

    _setup_account(mock_db, cash=30_000)

    create_transaction("acc1", TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="BUY",
        price=10.0, amount=1000, date="2025-01-15"
    ))
    create_transaction("acc1", TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="SPLIT",
        price=0.0, amount=100, date="2025-01-16"
    ))
    create_transaction("acc1", TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="RIGHTS",
        price=8.0, amount=100, date="2025-01-17", fee=2.0
    ))

    summary = get_stock_summary("acc1", "000001.SZ")
    data = summary["data"]

    assert data["current_quantity"] == 1200
    assert abs(data["total_buy_amount"] - 10807.2) < 0.01
    assert abs(data["avg_cost"] - 9.82) < 0.01
    assert abs(data["realized_pnl"] - 0.0) < 0.01


# ─────────────────────────────────────────────────────────────
# 9. 已有持仓再买入（加仓加权平均）
# ─────────────────────────────────────────────────────────────

def test_add_position_weighted_average(mock_db):
    """加仓: 新均价 = (原总成本 + 新买入成本) / 新总股数"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    _setup_account(mock_db, cash=200_000)
    _setup_holding(mock_db, quantity=1000, cost_price=10.0, total_cost=10000.0)

    # 加仓 500 股 @ 12元
    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="BUY",
        price=12.0, amount=500, date="2025-02-01"
    )
    result = create_transaction("acc1", trans)
    fee = result["fee"]

    holding = _get_holding(mock_db)
    assert holding["quantity"] == 1500, "加仓后总量 = 1000 + 500"

    # 新总成本 = 原 10000 + (12*500 + fee) = 10000 + 6000 + fee
    buy_cost = 12.0 * 500 + fee
    expected_total = 10000.0 + buy_cost
    expected_cost_price = expected_total / 1500

    assert abs(holding["total_cost"] - round(expected_total, 2)) < 0.01
    assert abs(holding["cost_price"] - round(expected_cost_price, 4)) < 0.01


# ─────────────────────────────────────────────────────────────
# 10. 无 broker 使用默认费率
# ─────────────────────────────────────────────────────────────

def test_default_rates_without_broker(mock_db):
    """无 broker 关联时使用默认费率: 0.0003 / 5.0 / 0.001"""
    from web.backend.api.transactions import create_transaction, TransactionCreate

    # 不关联 broker
    _setup_account(mock_db, cash=200_000, broker_id=None)

    # 买入: 100000 * 0.0003 = 30 > 5，再叠加过户费 2.0 → fee=32
    trans = TransactionCreate(
        ts_code="000001.SZ", name="平安", trans_type="BUY",
        price=100.0, amount=1000, date="2025-01-15"
    )
    result = create_transaction("acc1", trans)
    assert result["fee"] == 32.0

    # 卖出验证印花税
    _setup_holding(mock_db, ts_code="600036.SH", name="招行",
                   quantity=1000, cost_price=50.0, total_cost=50000.0)
    trans2 = TransactionCreate(
        ts_code="600036.SH", name="招行", trans_type="SELL",
        price=60.0, amount=1000, date="2025-01-20"
    )
    result2 = create_transaction("acc1", trans2)
    expected_stamp = 60.0 * 1000 * 0.001  # 60.0
    assert result2["stamp_tax"] == expected_stamp
