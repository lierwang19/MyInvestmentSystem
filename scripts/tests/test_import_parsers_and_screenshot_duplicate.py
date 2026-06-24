# -*- coding: utf-8 -*-
"""
导入解析层回归测试：
1. 平安 CSV 的“红股派息”应按送股 + 现金分红拆成两笔
2. 华林 CSV 的“红利入账/配股缴款/股息红利差异扣税”应映射到正确交易语义
3. 截图导入二次去重应区分 new / maybe_duplicate / duplicate
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from web.backend.api.import_flow import parse_hualin_csv, parse_pingan_csv
from web.backend.api.screenshot_import import _check_duplicate


def test_pingan_csv_bonus_issue_splits_into_split_and_dividend():
    content = """记账日期,业务摘要,资产账户,股东帐号,证券代码,证券名称,成交股数,成交价格,成交金额,利息金额,发生金额,资金余额,手续费,印花税,过户费等,财汇局交易征费,币种,港股汇率
2026-04-15,红股派息,acct,holder,600000,浦发银行,100,0,0,0,23.45,0,0,0,0,0,CNY,1
""".encode("utf-8")

    transactions, capital_flows, warnings, skipped = parse_pingan_csv(content)

    assert capital_flows == []
    assert warnings == []
    assert skipped == []
    assert len(transactions) == 2

    split_tx = next(tx for tx in transactions if tx["trans_type"] == "SPLIT")
    dividend_tx = next(tx for tx in transactions if tx["trans_type"] == "DIVIDEND")

    assert split_tx["ts_code"] == "600000.SH"
    assert split_tx["amount"] == 100
    assert split_tx["total_amt"] == 0.0

    assert dividend_tx["ts_code"] == "600000.SH"
    assert dividend_tx["amount"] == 0
    assert dividend_tx["total_amt"] == 23.45
    assert dividend_tx["remarks"] == "红股派息"


def test_hualin_csv_maps_dividend_rights_and_tax_correctly():
    content = """日    期,业务标志,证券代码,证券名称,价    格,发 生 数,发生金额,手 续 费,印 花 税,过 户 费,财汇局征费
2026-04-15,红利入账,600000,浦发银行,0,1200,36.00,0,0,0,0
2026-04-16,配股缴款,600000,浦发银行,8,100,-802.00,2,0,0,0
2026-04-17,股息红利差异扣税,600000,浦发银行,0,0,-3.60,0,0,0,0
""".encode("utf-8")

    transactions, capital_flows, warnings, skipped = parse_hualin_csv(content)

    assert capital_flows == []
    assert warnings == []
    assert skipped == []
    assert len(transactions) == 3

    dividend_tx = next(tx for tx in transactions if tx["trans_type"] == "DIVIDEND")
    rights_tx = next(tx for tx in transactions if tx["trans_type"] == "RIGHTS")
    tax_tx = next(tx for tx in transactions if tx["trans_type"] == "DIVIDEND_TAX")

    assert dividend_tx["amount"] == 0
    assert dividend_tx["price"] == 0.0
    assert dividend_tx["total_amt"] == 36.0

    assert rights_tx["amount"] == 100
    assert rights_tx["price"] == 8.0
    assert rights_tx["total_amt"] == 800.0
    assert rights_tx["fee"] == 2.0
    assert rights_tx["remarks"] == "配股缴款"

    assert tax_tx["amount"] == 0
    assert tax_tx["total_amt"] == 3.6
    assert tax_tx["remarks"] == "股息红利差异扣税"


def test_screenshot_duplicate_check_distinguishes_duplicate_levels():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            trans_type TEXT NOT NULL,
            date TEXT NOT NULL,
            price REAL NOT NULL,
            amount INTEGER NOT NULL,
            remarks TEXT
        )
        """
    )
    c.execute(
        """
        INSERT INTO transactions (account_id, ts_code, trans_type, date, price, amount, remarks)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("acc1", "600000.SH", "BUY", "2026-04-15", 10.0, 100, "截图导入 10:31"),
    )
    conn.commit()

    assert _check_duplicate(conn, "acc1", "600000.SH", "BUY", "2026-04-15", 10.0, 100, "11:05") == "maybe_duplicate"
    assert _check_duplicate(conn, "acc1", "600000.SH", "BUY", "2026-04-15", 10.0, 100, "10:31") == "duplicate"
    assert _check_duplicate(conn, "acc1", "600000.SH", "BUY", "2026-04-15", 10.2, 100, "10:31") == "new"

    conn.close()
