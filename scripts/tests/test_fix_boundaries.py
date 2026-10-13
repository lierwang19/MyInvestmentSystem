"""Boundary tests for the 2026-10-08 audit fixes (transactions, capital flows, PIT valuation)."""
import pytest
from fastapi import HTTPException

from web.backend import database
from web.backend.api import accounts_db, transactions
from scripts.etl.build_fundamentals_and_valuation import statutory_disclosure_deadline


@pytest.fixture
def conn():
    with database.get_db() as c:
        c.execute("INSERT INTO accounts(id,name,cash_balance) VALUES ('b','b',10000)")
        c.execute("INSERT INTO holdings(account_id,ts_code,quantity,cost_price,total_cost) "
                  "VALUES ('b','000001.SZ',100,10,1000)")
        c.commit()
        yield c


def _trade(kind, amount, **kw):
    return transactions.TransactionCreate(ts_code="000001.SZ", name="测试", trans_type=kind,
                                          price=kw.pop("price", 10), amount=amount,
                                          date=kw.pop("date", "2026-10-08"), **kw)


def test_negative_sell_amount_within_holding_is_accepted(conn):
    # 前端 SELL 传负数数量，必须继续被接受
    transactions.create_transaction("b", _trade("SELL", -100))
    assert conn.execute("SELECT quantity FROM holdings WHERE account_id='b'").fetchone()[0] == 0


@pytest.mark.parametrize("kw", [{"price": float("nan")}, {"price": float("inf")},
                                {"fee": -1}, {"date": "2026/10/08"}])
def test_invalid_numbers_and_dates_rejected(conn, kw):
    with pytest.raises(HTTPException) as err:
        transactions.create_transaction("b", _trade("BUY", 100, **kw))
    assert err.value.status_code == 400
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == 10000


def test_capital_flow_delete_reverses_cash_and_keeps_holdings(conn):
    res = accounts_db.add_capital_flow("b", accounts_db.CapitalFlowCreate(
        flow_type="WITHDRAW", amount=300, date="2026-10-08"))
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == 9700
    accounts_db.delete_capital_flow(res["id"])
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == 10000
    assert conn.execute("SELECT quantity FROM holdings WHERE account_id='b'").fetchone()[0] == 100


@pytest.mark.parametrize("amount", [0, -5, float("nan")])
def test_capital_flow_rejects_non_positive_amount(conn, amount):
    with pytest.raises(HTTPException):
        accounts_db.add_capital_flow("b", accounts_db.CapitalFlowCreate(
            flow_type="DEPOSIT", amount=amount, date="2026-10-08"))
    assert conn.execute("SELECT COUNT(*) FROM capital_flows").fetchone()[0] == 0


@pytest.mark.parametrize("period_end,expected", [
    ("2026-03-31", "2026-04-30"), ("2026-06-30", "2026-08-31"),
    ("2026-09-30", "2026-10-31"), ("2025-12-31", "2026-04-30"),
])
def test_statutory_disclosure_deadline(period_end, expected):
    assert statutory_disclosure_deadline(period_end) == expected


# --- 2026-10-09 review follow-ups ---

def test_backdate_refused_when_history_incomplete(conn):
    # 快照 100 股（无交易历史）+ 正常买入 100 股，再补录更早买入：不能覆盖快照基线
    transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-08"))
    with pytest.raises(HTTPException) as err:
        transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-01"))
    assert err.value.status_code == 409
    assert conn.execute("SELECT quantity FROM holdings WHERE account_id='b'").fetchone()[0] == 200
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_backdated_sell_checks_quantity_at_that_date(conn):
    conn.execute("DELETE FROM holdings")
    conn.commit()
    transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-08"))
    cash = conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0]
    with pytest.raises(HTTPException) as err:
        transactions.create_transaction("b", _trade("SELL", -100, date="2026-10-01"))
    assert err.value.status_code == 400
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == cash
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_backdated_buy_with_complete_history_allowed(conn):
    conn.execute("DELETE FROM holdings")
    conn.commit()
    transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-08"))
    transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-01"))
    assert conn.execute("SELECT quantity FROM holdings WHERE account_id='b'").fetchone()[0] == 200


def test_concurrent_capital_flow_delete_reverses_once(conn):
    import threading
    res = accounts_db.add_capital_flow("b", accounts_db.CapitalFlowCreate(
        flow_type="DEPOSIT", amount=100, date="2026-10-08"))
    barrier = threading.Barrier(2)
    outcomes = []

    def worker():
        barrier.wait()
        try:
            accounts_db.delete_capital_flow(res["id"])
            outcomes.append("ok")
        except HTTPException as e:
            outcomes.append(e.status_code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes, key=str) == [404, "ok"]
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == 10000


@pytest.mark.parametrize("bad", ["2026-2-1", "2026-02-1", "2026-10-08 ", "20261008"])
def test_non_padded_dates_rejected(conn, bad):
    with pytest.raises(HTTPException):
        transactions.create_transaction("b", _trade("BUY", 100, date=bad))
    with pytest.raises(HTTPException):
        accounts_db.add_capital_flow("b", accounts_db.CapitalFlowCreate(
            flow_type="DEPOSIT", amount=1, date=bad))
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM capital_flows").fetchone()[0] == 0


class _Upload:
    def __init__(self, data):
        self.filename, self._data = "backup.zip", data

    async def read(self, *args):
        return self._data


def _restore_zip(tmp_path, extra):
    import io
    import sqlite3
    import zipfile
    src_db = tmp_path / "backup_src.db"
    c = sqlite3.connect(src_db)
    database._create_tables(c)
    c.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(src_db, "data/user_data.db")
        for name, body in extra:
            zf.writestr(name, body)
    return buf.getvalue()


@pytest.fixture
def restore_root(tmp_path, monkeypatch):
    from web.backend.api import system
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config" / "a.yaml").write_text("old")
    monkeypatch.setattr(system, "PROJECT_ROOT", str(root))
    monkeypatch.setattr(system, "USER_DB_PATH", str(root / "data" / "user_data.db"))
    return root


def test_restore_failure_in_staging_publishes_nothing(tmp_path, restore_root):
    import asyncio
    from web.backend.api import system
    (restore_root / "config" / "sub").mkdir()
    data = _restore_zip(tmp_path, [("config/a.yaml", "new"), ("config/sub", "conflict")])
    res = asyncio.run(system.restore_system_data(_Upload(data)))
    assert res["status"] == "error"
    assert not (restore_root / "data" / "user_data.db.restore-pending").exists()
    assert (restore_root / "config" / "a.yaml").read_text() == "old"
    assert not [p for p in restore_root.rglob("*.restore-*")]


def test_restore_failure_on_publish_rolls_back_config_and_keeps_old_pending(tmp_path, restore_root, monkeypatch):
    import asyncio
    from web.backend.api import system
    pending = restore_root / "data" / "user_data.db.restore-pending"
    pending.write_bytes(b"previous-pending")
    real_replace = system.os.replace

    def failing_replace(src, dst):
        if str(dst).endswith(".restore-pending"):
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(system.os, "replace", failing_replace)
    data = _restore_zip(tmp_path, [("config/a.yaml", "new"), ("config/b.yaml", "added")])
    res = asyncio.run(system.restore_system_data(_Upload(data)))
    assert res["status"] == "error"
    assert pending.read_bytes() == b"previous-pending"
    assert (restore_root / "config" / "a.yaml").read_text() == "old"
    assert not (restore_root / "config" / "b.yaml").exists()
    assert not [p for p in restore_root.rglob("*.restore-tmp")] + [p for p in restore_root.rglob("*.restore-bak")]


def test_restore_success_publishes_db_and_config(tmp_path, restore_root):
    import asyncio
    from web.backend.api import system
    data = _restore_zip(tmp_path, [("config/a.yaml", "new")])
    res = asyncio.run(system.restore_system_data(_Upload(data)))
    assert res["status"] == "success", res
    assert (restore_root / "data" / "user_data.db.restore-pending").exists()
    assert (restore_root / "config" / "a.yaml").read_text() == "new"


# --- 2026-10-09 second review follow-ups ---

def test_backdate_refused_after_snapshot_position_cleared(conn):
    # 快照 100 股 → 全部卖出 → 补录更早买入：历史缺 BUY，清仓后两边都是 0，也必须拒绝
    transactions.create_transaction("b", _trade("SELL", -100, date="2026-10-08"))
    cash = conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0]
    with pytest.raises(HTTPException) as err:
        transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-01"))
    assert err.value.status_code == 409
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == cash
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_restore_cleanup_failure_after_publish_still_reports_success(tmp_path, restore_root, monkeypatch):
    import asyncio
    from web.backend.api import system
    real_unlink = system.os.unlink

    def failing_unlink(path, *args, **kwargs):
        if str(path).endswith(".restore-bak"):
            raise OSError("permission denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(system.os, "unlink", failing_unlink)
    data = _restore_zip(tmp_path, [("config/a.yaml", "new")])
    res = asyncio.run(system.restore_system_data(_Upload(data)))
    assert res["status"] == "success", res
    assert (restore_root / "data" / "user_data.db.restore-pending").exists()
    assert (restore_root / "config" / "a.yaml").read_text() == "new"


def test_restore_rollback_failure_keeps_original_config_backup(tmp_path, restore_root, monkeypatch):
    # 发布失败 + 配置回滚失败：原配置备份必须保留，并在错误信息中给出路径
    import asyncio
    from web.backend.api import system
    real_replace = system.os.replace

    def failing_replace(src, dst):
        if str(dst).endswith(".restore-pending"):
            raise OSError("disk full")
        if str(src).endswith(".restore-bak"):
            raise OSError("rollback failed")
        return real_replace(src, dst)

    monkeypatch.setattr(system.os, "replace", failing_replace)
    data = _restore_zip(tmp_path, [("config/a.yaml", "new")])
    res = asyncio.run(system.restore_system_data(_Upload(data)))
    assert res["status"] == "error"
    backups = list((restore_root / "config").glob("*.restore-bak"))
    assert len(backups) == 1 and backups[0].read_text() == "old"
    assert str(backups[0]) in res["message"]
    assert not (restore_root / "data" / "user_data.db.restore-pending").exists()
    assert not list(restore_root.rglob("*.restore-tmp"))


# --- delete transaction must not overwrite snapshot baseline ---

def test_delete_on_snapshot_holding_keeps_baseline_and_reverses_cash(conn):
    # 快照 100 股 + 正常买入 100 股，删除这笔买入：现金冲销，但持仓不能被重算成 0/删除
    res = transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-08"))
    out = transactions.delete_transaction(res["id"])
    assert out["holdings_rebuilt"] is False and "手工核对" in out["warning"]
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == pytest.approx(10000)
    assert conn.execute("SELECT quantity FROM holdings WHERE account_id='b'").fetchone()[0] == 200
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_delete_with_complete_history_still_rebuilds(conn):
    conn.execute("DELETE FROM holdings")
    conn.commit()
    first = transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-01"))
    transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-08"))
    out = transactions.delete_transaction(first["id"])
    assert out["holdings_rebuilt"] is True
    assert conn.execute("SELECT quantity FROM holdings WHERE account_id='b'").fetchone()[0] == 100


def test_delete_same_transaction_twice_reverses_once(conn):
    conn.execute("DELETE FROM holdings")
    conn.commit()
    res = transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-08"))
    transactions.delete_transaction(res["id"])
    with pytest.raises(HTTPException) as err:
        transactions.delete_transaction(res["id"])
    assert err.value.status_code == 404
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == pytest.approx(10000)


def test_delete_rights_returns_cash(conn):
    # 配股创建扣现金（含费用），删除必须原额退回
    conn.execute("DELETE FROM holdings")
    conn.commit()
    transactions.create_transaction("b", _trade("BUY", 100, date="2026-10-01"))
    cash_before = conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0]
    res = transactions.create_transaction("b", _trade("RIGHTS", 30, price=8, fee=1.5, date="2026-10-08"))
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == pytest.approx(cash_before - 241.5)
    out = transactions.delete_transaction(res["id"])
    assert out["cash_reversed"] == pytest.approx(241.5)
    assert conn.execute("SELECT cash_balance FROM accounts WHERE id='b'").fetchone()[0] == pytest.approx(cash_before)
    assert conn.execute("SELECT quantity FROM holdings WHERE account_id='b'").fetchone()[0] == 100
