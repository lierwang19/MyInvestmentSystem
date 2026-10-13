"""market.db 连接拆分：get_market_db() 只读，合法写入走 get_market_db_rw()。"""
import ast
import re
import sqlite3
from pathlib import Path

import pytest

from web.backend import database

ROOT = Path(__file__).resolve().parents[2]
WRITE_SQL = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER|VACUUM|ATTACH)\b"
                       r"|\bPRAGMA\s+journal_mode\s*=", re.I)


@pytest.fixture
def market_db(monkeypatch, tmp_path):
    # 路径含中文和空格，与真实部署路径（/Volumes/外挂硬盘/...）一致
    path = tmp_path / "外挂 硬盘" / "market.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT)")
    conn.execute("INSERT INTO daily_price VALUES ('000001.SZ', '2026-10-09')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(database, "MARKET_DB_PATH", str(path))
    return path


def test_read_only_connection_reads_but_rejects_writes(market_db):
    with database.get_market_db() as conn:
        assert conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0] == "2026-10-09"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO daily_price VALUES ('600000.SH', '2026-10-10')")


def test_read_write_connection_can_write(market_db):
    with database.get_market_db_rw() as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_price_trade_date ON daily_price(trade_date)")
        conn.execute("INSERT INTO daily_price VALUES ('600000.SH', '2026-10-10')")
        conn.commit()
    with database.get_market_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0] == 2


def test_missing_market_db_yields_none(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "MARKET_DB_PATH", str(tmp_path / "missing.db"))
    for factory in (database.get_market_db, database.get_market_db_rw):
        with factory() as conn:
            assert conn is None
    assert not (tmp_path / "missing.db").exists()


def test_read_only_blocks_contain_no_write_sql():
    """防回归：新增写入必须改用 get_market_db_rw()，不能写在只读块里。"""
    offenders = []
    for path in (ROOT / "web" / "backend").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            if not any(isinstance(i.context_expr, ast.Call)
                       and getattr(i.context_expr.func, "id", None) == "get_market_db"
                       for i in node.items):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and WRITE_SQL.search(sub.value):
                    offenders.append(f"{path.relative_to(ROOT)}:{sub.lineno}")
    assert offenders == []
