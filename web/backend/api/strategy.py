# -*- coding: utf-8 -*-
"""
Strategy API — 策略管理与运行
==============================
Endpoints:
  GET  /api/strategy/list              获取所有策略
  GET  /api/strategy/{id}/config       获取策略配置
  PUT  /api/strategy/{id}/config       更新策略配置
  POST /api/strategy/{id}/run          手动运行策略
  GET  /api/strategy/{id}/signals      获取最近信号
  GET  /api/strategy/{id}/decisions    获取最近决策
  GET  /api/strategy/{id}/runs         获取运行历史
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import json
import os
import logging

import yaml

from web.backend.config import PROJECT_ROOT
from web.backend.database import get_db
from web.backend.utils import dict_factory
from web.backend.time_utils import sh_timestamp_str

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Models ──

class StrategyConfigUpdate(BaseModel):
    config_yaml: str  # Full YAML content to save


# ── GET /api/strategy/list ──

@router.get("/strategy/list")
def list_strategies():
    """获取所有策略列表"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()
        c.execute("SELECT * FROM strategies ORDER BY created_at")
        rows = c.fetchall()
        return {"status": "success", "data": rows}


# ── GET /api/strategy/{strategy_id}/config ──

@router.get("/strategy/{strategy_id}/config")
def get_strategy_config(strategy_id: str):
    """获取策略配置详情（含 YAML 配置文件内容）"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()
        c.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
        strategy = c.fetchone()
        if not strategy:
            raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")

        # Read YAML config
        config_content = {}
        config_yaml_text = ""
        config_path = os.path.join(PROJECT_ROOT, strategy.get("config_path", ""))
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config_yaml_text = f.read()
            config_content = yaml.safe_load(config_yaml_text) or {}

        return {
            "status": "success",
            "data": {
                "strategy": strategy,
                "config": config_content,
                "config_yaml": config_yaml_text,
            }
        }


# ── PUT /api/strategy/{strategy_id}/config ──

@router.put("/strategy/{strategy_id}/config")
def update_strategy_config(strategy_id: str, body: StrategyConfigUpdate):
    """更新策略配置（保存 YAML 文件）"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()
        c.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
        strategy = c.fetchone()
        if not strategy:
            raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")

        config_path = os.path.join(PROJECT_ROOT, strategy.get("config_path", ""))

        # Validate YAML
        try:
            yaml.safe_load(body.config_yaml)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

        # Save
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(body.config_yaml)

        # Update timestamp
        c.execute("UPDATE strategies SET updated_at = ? WHERE id = ?", (sh_timestamp_str(), strategy_id))
        conn.commit()

        return {"status": "success", "message": "配置已保存"}


# ── POST /api/strategy/{strategy_id}/run ──

@router.post("/strategy/{strategy_id}/run")
def run_strategy_endpoint(strategy_id: str, account_id: Optional[str] = Query(None)):
    """手动运行策略，生成信号和决策"""
    try:
        import engine as strategy_engine

        result = strategy_engine.run_strategy(
            strategy_id=strategy_id,
            account_id=account_id or "",
            project_root=PROJECT_ROOT,
        )

        if result.error:
            raise HTTPException(status_code=400, detail=result.error)

        buy_decisions = [d for d in result.decisions if d.action == "BUY"]
        sell_decisions = [d for d in result.decisions if d.action == "SELL"]
        blocked_decisions = [d for d in result.decisions if d.action == "NO_ACTION"]

        buy_signals = [s for s in result.signals if s.signal == "BUY_CANDIDATE"]
        sell_signals = [s for s in result.signals if s.signal == "SELL_CANDIDATE"]

        return {
            "status": "success",
            "message": f"策略运行完成",
            "data": {
                "run_id": result.run_id,
                "run_date": result.run_date,
                "summary": {
                    "total_signals": len(result.signals),
                    "buy_candidates": len(buy_signals),
                    "sell_candidates": len(sell_signals),
                    "buy_decisions": len(buy_decisions),
                    "sell_decisions": len(sell_decisions),
                    "blocked": len(blocked_decisions),
                },
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Strategy run failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/strategy/{strategy_id}/signals ──

@router.get("/strategy/{strategy_id}/signals")
def get_strategy_signals(
    strategy_id: str,
    account_id: Optional[str] = Query(None),
    run_id: Optional[int] = Query(None),
    signal_filter: Optional[str] = Query(None, alias="signal"),
):
    """获取策略信号列表（默认最近一次运行；account_id 不传则取最新一次无论账户）"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()

        # Get run_id（按 account_id 过滤）
        if run_id is None:
            if account_id:
                c.execute("""
                    SELECT id FROM strategy_runs
                    WHERE strategy_id = ? AND account_id = ?
                    ORDER BY created_at DESC LIMIT 1
                """, (strategy_id, account_id))
            else:
                c.execute("""
                    SELECT id FROM strategy_runs
                    WHERE strategy_id = ?
                    ORDER BY created_at DESC LIMIT 1
                """, (strategy_id,))
            row = c.fetchone()
            if not row:
                return {"status": "success", "data": [], "run_id": None}
            run_id = row["id"]

        # Get signals
        query = "SELECT * FROM strategy_signals WHERE run_id = ?"
        params = [run_id]
        if signal_filter:
            query += " AND signal = ?"
            params.append(signal_filter)
        query += " ORDER BY score DESC"

        c.execute(query, params)
        rows = c.fetchall()

        for row in rows:
            if row.get("explain_json"):
                try:
                    row["explain"] = json.loads(row["explain_json"])
                except Exception:
                    row["explain"] = {}

        return {"status": "success", "data": rows, "run_id": run_id}


# ── GET /api/strategy/{strategy_id}/decisions ──

@router.get("/strategy/{strategy_id}/decisions")
def get_strategy_decisions(
    strategy_id: str,
    account_id: Optional[str] = Query(None),
    run_id: Optional[int] = Query(None),
):
    """获取策略决策列表（默认最近一次运行；account_id 不传则取最新一次无论账户）"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()

        if run_id is None:
            if account_id:
                c.execute("""
                    SELECT id FROM strategy_runs
                    WHERE strategy_id = ? AND account_id = ?
                    ORDER BY created_at DESC LIMIT 1
                """, (strategy_id, account_id))
            else:
                c.execute("""
                    SELECT id FROM strategy_runs
                    WHERE strategy_id = ?
                    ORDER BY created_at DESC LIMIT 1
                """, (strategy_id,))
            row = c.fetchone()
            if not row:
                return {"status": "success", "data": [], "run_id": None}
            run_id = row["id"]

        c.execute("""
            SELECT * FROM strategy_decisions
            WHERE run_id = ?
            ORDER BY
                CASE action WHEN 'SELL' THEN 0 WHEN 'BUY' THEN 1 ELSE 2 END,
                priority DESC
        """, (run_id,))
        rows = c.fetchall()

        for row in rows:
            if row.get("explain_json"):
                try:
                    row["explain"] = json.loads(row["explain_json"])
                except Exception:
                    row["explain"] = {}

        return {"status": "success", "data": rows, "run_id": run_id}


# ── GET /api/strategy/{strategy_id}/runs ──

@router.get("/strategy/{strategy_id}/runs")
def get_strategy_runs(
    strategy_id: str,
    account_id: Optional[str] = Query(None),
    limit: int = Query(20),
):
    """获取策略运行历史（可按 account_id 过滤）"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()
        if account_id:
            c.execute("""
                SELECT * FROM strategy_runs
                WHERE strategy_id = ? AND account_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (strategy_id, account_id, limit))
        else:
            c.execute("""
                SELECT * FROM strategy_runs
                WHERE strategy_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (strategy_id, limit))
        rows = c.fetchall()
        return {"status": "success", "data": rows}
