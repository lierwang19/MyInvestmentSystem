"""Audit acceptance tests: failures document defects for the developer to fix.

Use real API handlers and scoring/ETL functions with disposable data. Do not
mark these xfail: a future fix must make the acceptance assertions pass.
"""
import sqlite3

import pandas as pd
import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from web.backend import database
from web.backend.api import accounts_db, transactions, upload
from scripts.scoring.scoring import run_scoring
from scripts.strategy.engine import generate_signals
from scripts.etl.build_fundamentals_and_valuation import compute_daily_valuation_for_stock


@pytest.fixture
def account_db():
    with database.get_db() as conn:
        conn.execute("INSERT INTO accounts(id,name,cash_balance) VALUES ('audit','audit',10000)")
        conn.commit()
        yield conn


def transaction(kind, price=10, amount=100, date="2026-10-08"):
    return transactions.TransactionCreate(ts_code="000001.SZ", name="测试股票",
        trans_type=kind, price=price, amount=amount, date=date)


@pytest.mark.parametrize("held", [0, 100])
def test_sell_cannot_exceed_available_holding(account_db, held):
    if held:
        account_db.execute("INSERT INTO holdings(account_id,ts_code,quantity,cost_price,total_cost) VALUES ('audit','000001.SZ',?,10,?)", (held, held * 10))
        account_db.commit()
    with pytest.raises(HTTPException) as error:
        transactions.create_transaction("audit", transaction("SELL", amount=200))
    assert error.value.status_code in (400, 409, 422)
    assert account_db.execute("SELECT cash_balance FROM accounts WHERE id='audit'").fetchone()[0] == 10000
    assert account_db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_failed_holdings_import_preserves_existing_rows(account_db, tmp_path):
    account_db.execute("INSERT INTO holdings(account_id,ts_code,quantity,cost_price,total_cost) VALUES ('audit','000001.SZ',100,10,1000)")
    account_db.commit()
    path = tmp_path / "invalid.csv"
    path.write_text("证券代码,证券名称,证券数量,成本价\n000001,测试股票,bad-number,10\n")
    with pytest.raises(ValueError):
        upload.migrate_holdings_to_db(str(path), "audit")
    row = account_db.execute("SELECT quantity,total_cost FROM holdings WHERE account_id='audit'").fetchone()
    assert row is not None, "导入失败不能删除原持仓"
    assert tuple(row) == (100, 1000)


def test_capital_flow_keeps_snapshot_only_holdings(account_db):
    account_db.execute("INSERT INTO holdings(account_id,ts_code,quantity,cost_price,total_cost) VALUES ('audit','000001.SZ',100,10,1000)")
    account_db.commit()
    accounts_db.add_capital_flow("audit", accounts_db.CapitalFlowCreate(
        flow_type="DEPOSIT", amount=100, date="2026-10-08"))
    row = account_db.execute("SELECT quantity FROM holdings WHERE account_id='audit'").fetchone()
    assert row is not None, "资金流水操作不能清空缺少交易历史的快照持仓"
    assert row[0] == 100
    assert account_db.execute("SELECT cash_balance FROM accounts WHERE id='audit'").fetchone()[0] == 10100


def test_backdated_trade_agrees_with_chronological_replay(account_db):
    for kind, price, date in [("BUY", 10, "2026-10-01"), ("SELL", 20, "2026-10-08"), ("BUY", 5, "2026-10-02")]:
        transactions.create_transaction("audit", transaction(kind, price=price, date=date))
    before = tuple(account_db.execute("SELECT quantity,total_cost,cost_price FROM holdings WHERE account_id='audit'").fetchone())
    transactions._rebuild_single_stock_holdings(account_db, "audit", "000001.SZ")
    after = tuple(account_db.execute("SELECT quantity,total_cost,cost_price FROM holdings WHERE account_id='audit'").fetchone())
    assert before == after, "同一交易历史不应因点击重建而改变持仓成本"


@pytest.mark.parametrize("kind,amount,account", [
    ("NOT_A_TYPE", 100, "audit"), ("BUY", 0, "audit"), ("BUY", 100, "missing"),
])
def test_api_rejects_invalid_transactions(account_db, kind, amount, account):
    # No lifespan context: do not start pollers, ETL or APScheduler.
    from web.backend.main import app
    client = TestClient(app)
    try:
        response = client.post(f"/api/transactions/{account}", json={
            "ts_code": "000001.SZ", "name": "测试股票", "trans_type": kind,
            "price": 10, "amount": amount, "date": "2026-10-08",
        })
    finally:
        client.close()
    assert response.status_code in (400, 404, 409, 422), response.text
    assert account_db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_missing_distance_does_not_poison_valid_valuation_scores(account_db, tmp_path):
    config = {
        "positives": {
            "weights": {"pb": .4, "pe_ttm": .4, "distance_from_1y_low": .2},
            "pb": {"field": "pb", "anchor_default": 1, "tiers": [{"delta": 0, "score": 100}, {"else": 0}]},
            "pe_ttm": {"field": "pe_ttm", "anchor_default": 10, "ignore_if_le_zero": True,
                       "tiers": [{"delta": 0, "score": 100}, {"else": 0}]},
            "distance_from_1y_low": {"field": "distance_from_1y_low", "buckets": [{"max": 1, "score": 100}, {"else": 0}]},
        },
    }
    config_path = tmp_path / "scoring.yaml"
    config_path.write_text(yaml.safe_dump(config))
    base_path = tmp_path / "base.csv"
    pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "name": ["测试一", "测试二"],
                  "anchor_industry": ["银行", "银行"], "tdx_l1": ["银行", "银行"]}).to_csv(base_path, index=False)
    enriched_path = tmp_path / "enriched.csv"
    pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "pb": [.5, .5],
                  "pe_ttm": [5, 5], "distance_from_1y_low": [None, .1]}).to_csv(enriched_path, index=False)
    result = run_scoring(str(config_path), str(base_path), str(enriched_path), database.DB_PATH,
                         min_score=70, write_csv=False)
    assert {row["ts_code"] for row in result["data"]} == {"000001.SZ", "000002.SZ"}


def test_missing_pb_does_not_abort_all_strategy_signals():
    signals = generate_signals({"sell_rules": {"method": "from_buy_pb"}},
        [{"ts_code": "000001.SZ", "rise_from_1y_low_pct": 50}],
        {"000001.SZ": {"buy_pb": 0}}, None, "2026-10-08")
    assert len(signals) == 1
    assert signals[0].signal in ("HOLD", "SKIP")


def test_unknown_announcement_date_is_not_point_in_time_data():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript("""
            CREATE TABLE fin_indicators_q(ts_code,period_end,announce_date,bvps,eps_ttm);
            CREATE TABLE daily_price(ts_code,trade_date,close);
            CREATE TABLE daily_valuation(ts_code,trade_date,pb,pe_ttm,method,PRIMARY KEY(ts_code,trade_date));
            INSERT INTO fin_indicators_q VALUES ('000001.SZ','2026-03-31',NULL,10,2);
            INSERT INTO daily_price VALUES ('000001.SZ','2026-04-01',10);
        """)
        compute_daily_valuation_for_stock(conn, "000001.SZ")
        row = conn.execute("SELECT pb,pe_ttm FROM daily_valuation").fetchone()
        assert row is None or row == (None, None), "公告日未知的数据不能作为历史当日已知财务指标"
    finally:
        conn.close()

