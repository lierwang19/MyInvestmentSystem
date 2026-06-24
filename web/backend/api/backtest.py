# -*- coding: utf-8 -*-
"""
Backtest API
============
POST /api/backtest/run            提交回测任务（同步运行）
GET  /api/backtest/list           获取回测列表
GET  /api/backtest/{id}           获取回测详情
GET  /api/backtest/{id}/nav       获取 NAV 曲线
GET  /api/backtest/{id}/trades    获取交易记录
DELETE /api/backtest/{id}         删除回测记录
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.backend.config import PROJECT_ROOT, MARKET_DB_PATH, USER_DB_PATH
from web.backend.database import get_db
from web.backend.utils import dict_factory

logger = logging.getLogger(__name__)
router = APIRouter()


class BacktestRunRequest(BaseModel):
    strategy_id: str = "dashu_v1"
    name: Optional[str] = None
    start_date: str       # YYYY-MM-DD
    end_date: str         # YYYY-MM-DD
    init_cash: float = 1_000_000.0


# ── POST /api/backtest/run ──

@router.post("/backtest/run")
def run_backtest_endpoint(body: BacktestRunRequest):
    """提交并同步运行回测（数据受限于 daily_valuation，约200支股票）"""
    import backtest as bt_module

    try:
        with get_db() as conn:
            c = conn.cursor()

            name = body.name or f"{body.strategy_id} {body.start_date}~{body.end_date}"
            c.execute(
                "INSERT INTO backtests (strategy_id, name, start_date, end_date, init_cash, status) "
                "VALUES (?, ?, ?, ?, ?, 'running')",
                (body.strategy_id, name, body.start_date, body.end_date, body.init_cash),
            )
            conn.commit()
            backtest_id = c.lastrowid

            result = bt_module.run_backtest(
                backtest_id=backtest_id,
                strategy_id=body.strategy_id,
                start_date=body.start_date,
                end_date=body.end_date,
                init_cash=body.init_cash,
                market_db_path=MARKET_DB_PATH,
                user_db_path=USER_DB_PATH,
                project_root=PROJECT_ROOT,
            )

            if result.error:
                c.execute(
                    "UPDATE backtests SET status='failed', result_json=? WHERE id=?",
                    (json.dumps({"error": result.error}), backtest_id),
                )
                conn.commit()
                raise HTTPException(status_code=400, detail=result.error)

            # Bulk insert NAV records
            c.executemany(
                "INSERT INTO backtest_nav "
                "(backtest_id, date, total_nav, cash, market_value, position_pct, holding_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (backtest_id, r["date"], r["total_nav"], r["cash"],
                     r["market_value"], r["position_pct"], r["holding_count"])
                    for r in result.nav_records
                ],
            )

            # Bulk insert trade records
            c.executemany(
                "INSERT INTO backtest_trades "
                "(backtest_id, date, ts_code, name, action, quantity, price, amount, "
                "commission, stamp_tax, reason_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (backtest_id, t["date"], t["ts_code"], t.get("name", ""),
                     t["action"], t["quantity"], t["price"], t["amount"],
                     t["commission"], t["stamp_tax"], t.get("reason_text", ""))
                    for t in result.trade_records
                ],
            )

            summary = {
                "final_nav": result.final_nav,
                "return_pct": result.return_pct,
                "max_drawdown_pct": result.max_drawdown_pct,
                "nav_count": len(result.nav_records),
                "trade_count": len(result.trade_records),
            }
            c.execute(
                "UPDATE backtests SET status='completed', result_json=? WHERE id=?",
                (json.dumps(summary), backtest_id),
            )
            conn.commit()

            return {"status": "success", "data": {"backtest_id": backtest_id, **summary}}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backtest run failed: {e}", exc_info=True)
        # Try to mark backtest as failed (best-effort, conn may already be closed)
        try:
            with get_db() as conn2:
                conn2.execute(
                    "UPDATE backtests SET status='failed', result_json=? WHERE id=?",
                    (json.dumps({"error": str(e)}), backtest_id),
                )
                conn2.commit()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/backtest/list ──

@router.get("/backtest/list")
def list_backtests():
    """获取回测列表（最近50条）"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()
        c.execute("SELECT * FROM backtests ORDER BY created_at DESC LIMIT 50")
        rows = c.fetchall()
    for row in rows:
        if row.get("result_json"):
            try:
                row["result"] = json.loads(row["result_json"])
            except Exception:
                row["result"] = {}
    return {"status": "success", "data": rows}


# ── GET /api/backtest/{id} ──

@router.get("/backtest/{backtest_id}")
def get_backtest(backtest_id: int):
    """获取单个回测详情"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()
        c.execute("SELECT * FROM backtests WHERE id = ?", (backtest_id,))
        row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Backtest not found")
    if row.get("result_json"):
        try:
            row["result"] = json.loads(row["result_json"])
        except Exception:
            row["result"] = {}
    return {"status": "success", "data": row}


# ── GET /api/backtest/{id}/nav ──

@router.get("/backtest/{backtest_id}/nav")
def get_backtest_nav(backtest_id: int):
    """获取 NAV 净值曲线"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()
        c.execute(
            "SELECT date, total_nav, cash, market_value, position_pct, holding_count "
            "FROM backtest_nav WHERE backtest_id = ? ORDER BY date",
            (backtest_id,),
        )
        rows = c.fetchall()
    return {"status": "success", "data": rows}


# ── GET /api/backtest/{id}/trades ──

@router.get("/backtest/{backtest_id}/trades")
def get_backtest_trades(backtest_id: int):
    """获取回测交易明细"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()
        c.execute(
            "SELECT * FROM backtest_trades WHERE backtest_id = ? ORDER BY date, action DESC",
            (backtest_id,),
        )
        rows = c.fetchall()
    return {"status": "success", "data": rows}


# ── DELETE /api/backtest/{id} ──

@router.delete("/backtest/{backtest_id}")
def delete_backtest(backtest_id: int):
    """删除回测及其 NAV / 交易记录"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM backtests WHERE id = ?", (backtest_id,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Backtest not found")
        c.execute("DELETE FROM backtest_nav WHERE backtest_id = ?", (backtest_id,))
        c.execute("DELETE FROM backtest_trades WHERE backtest_id = ?", (backtest_id,))
        c.execute("DELETE FROM backtest_positions WHERE backtest_id = ?", (backtest_id,))
        c.execute("DELETE FROM backtests WHERE id = ?", (backtest_id,))
        conn.commit()
    return {"status": "success", "message": "回测已删除"}
