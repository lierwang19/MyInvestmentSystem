# -*- coding: utf-8 -*-
"""
T-Trading Items API — 做T观察清单（支持 BUY/SELL 双模式）
提供：查询清单、批量导入、单条导入、软删除、清空
"""
import csv
import os
import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from web.backend.config import PROJECT_ROOT
from web.backend.database import get_db
from web.backend.time_utils import sh_now, sh_timestamp_str

logger = logging.getLogger(__name__)
router = APIRouter()


def _load_industry_map() -> dict:
    """从 stock_industry_base.csv 加载行业映射（ts_code -> {industry_l1, industry}）"""
    ind_map: dict = {}
    base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    try:
        with open(base_path, 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                code = row.get('ts_code', '').strip()
                if code:
                    ind_map[code] = {
                        'industry_l1': row.get('tdx_l1', '') or '',
                        'industry':    row.get('tdx_l2', '') or '',
                    }
    except Exception:
        pass
    return ind_map


class ImportDaysRequest(BaseModel):
    days: int = 30
    side: str = 'BUY'


class ImportOneRequest(BaseModel):
    transaction_id: int
    side: str = 'BUY'


# ── GET: 获取账户做T条目（按 side 筛选）──

@router.get("/t-trading/{account_id}")
def get_t_trading_items(account_id: str, side: str = Query('BUY')):
    """获取该账户做T观察清单条目（按 trade_side 筛选，INNER JOIN 过滤孤儿，LEFT JOIN 带真实持仓量）"""
    # 校验 side 参数
    if side not in ('BUY', 'SELL'):
        raise HTTPException(status_code=400, detail="side must be 'BUY' or 'SELL'")

    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT
                ti.id,
                ti.ts_code,
                ti.name,
                ti.buy_price,
                ti.buy_quantity,
                ti.buy_date,
                ti.trade_side,
                ti.transaction_id,
                h.quantity AS holding_quantity
            FROM t_trading_items ti
            INNER JOIN transactions t ON t.id = ti.transaction_id
            LEFT JOIN holdings h
                ON h.account_id = ti.account_id AND h.ts_code = ti.ts_code
            WHERE ti.account_id = ?
              AND ti.trade_side = ?
              AND ti.removed_at IS NULL
            ORDER BY ti.buy_date DESC, ti.id DESC
        ''', (account_id, side))
        rows = c.fetchall()

    # 行业名称 + 权重：优先从轻量共享函数取（与持仓分析页口径一致）
    # weight_map: {ts_code -> {industry_l1, industry, industry_l1_actual, industry_actual}}
    # ind_map: 兜底，用于已清仓但仍在做T列表的股票（只有名称，无权重）
    ind_map = _load_industry_map()
    weight_map: dict = {}
    try:
        from web.backend.api.analysis import get_industry_weight_map
        weight_map = get_industry_weight_map(account_id)
    except Exception as e:
        logger.warning(f"[t_trading] get_industry_weight_map 失败，行业权重将为空: {e}")

    items = []
    for row in rows:
        code = row["ts_code"]
        wt = weight_map.get(code)
        if wt:
            industry_l1        = wt["industry_l1"]
            industry           = wt["industry"]
            industry_l1_actual = wt["industry_l1_actual"]
            industry_actual    = wt["industry_actual"]
        else:
            ind = ind_map.get(code, {})
            industry_l1        = ind.get("industry_l1", "")
            industry           = ind.get("industry", "")
            industry_l1_actual = 0.0
            industry_actual    = 0.0
        items.append({
            "id": row["id"],
            "transaction_id": row["transaction_id"],
            "ts_code": code,
            "name": row["name"],
            "trade_side": row["trade_side"],
            "industry_l1":        industry_l1,
            "industry":           industry,
            "industry_l1_actual": industry_l1_actual,
            "industry_actual":    industry_actual,
            # 统一别名：前端可直接使用 base_price/base_quantity/base_date
            "base_price": row["buy_price"],
            "base_quantity": row["buy_quantity"],
            "base_date": row["buy_date"],
            # 保留原始字段名以兼容
            "buy_price": row["buy_price"],
            "buy_quantity": row["buy_quantity"],
            "buy_date": row["buy_date"],
            "holding_quantity": row["holding_quantity"] if row["holding_quantity"] is not None else 0,
        })

    return {"status": "success", "data": items}


# ── POST: 批量导入近 N 天交易 ──

@router.post("/t-trading/{account_id}/import")
def import_t_trading_items(account_id: str, req: ImportDaysRequest):
    """按天数批量导入该账户的 BUY/SELL 交易到做T观察清单（INSERT OR IGNORE 防重复）"""
    side = req.side
    if side not in ('BUY', 'SELL'):
        raise HTTPException(status_code=400, detail="side must be 'BUY' or 'SELL'")

    # BUY 导入 trans_type='BUY'，SELL 导入 trans_type='SELL'
    trans_type = side
    cutoff_date = (sh_now() - timedelta(days=req.days)).strftime("%Y-%m-%d")

    with get_db() as conn:
        c = conn.cursor()

        # 查询符合条件的交易
        c.execute('''
            SELECT id, ts_code, name, price, amount, date
            FROM transactions
            WHERE account_id = ? AND trans_type = ? AND date >= ?
            ORDER BY date DESC
        ''', (account_id, trans_type, cutoff_date))
        rows = c.fetchall()

        imported = 0
        for row in rows:
            result = c.execute('''
                INSERT INTO t_trading_items
                    (account_id, transaction_id, ts_code, name, buy_price, buy_quantity, buy_date, trade_side)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, transaction_id, trade_side) DO UPDATE SET removed_at = NULL
                    WHERE t_trading_items.removed_at IS NOT NULL
            ''', (account_id, row["id"], row["ts_code"], row["name"],
                  abs(row["price"]), abs(row["amount"]), row["date"], side))
            if result.rowcount > 0:
                imported += 1

        # 当前总条目数（该 side 未删除）
        c.execute('SELECT COUNT(*) FROM t_trading_items WHERE account_id = ? AND trade_side = ? AND removed_at IS NULL',
                  (account_id, side))
        total = c.fetchone()[0]

        conn.commit()

    label = "买入" if side == "BUY" else "卖出"
    return {
        "status": "success",
        "imported": imported,
        "total": total,
        "message": f"导入 {imported} 条{label}记录，当前清单共 {total} 条"
    }


# ── POST: 单条导入（交易成功后自动调用）──

@router.post("/t-trading/{account_id}/import-one")
def import_one_t_trading_item(account_id: str, req: ImportOneRequest):
    """按 transaction_id 单条导入到做T观察清单"""
    side = req.side
    if side not in ('BUY', 'SELL'):
        raise HTTPException(status_code=400, detail="side must be 'BUY' or 'SELL'")

    with get_db() as conn:
        c = conn.cursor()

        # 查询并校验交易
        c.execute('SELECT * FROM transactions WHERE id = ?', (req.transaction_id,))
        row = c.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Transaction not found")
        if row["account_id"] != account_id:
            raise HTTPException(status_code=404, detail="Transaction not found in this account")
        if row["trans_type"] != side:
            raise HTTPException(status_code=400, detail=f"Transaction type {row['trans_type']} does not match requested side {side}")

        result = c.execute('''
            INSERT INTO t_trading_items
                (account_id, transaction_id, ts_code, name, buy_price, buy_quantity, buy_date, trade_side)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, transaction_id, trade_side) DO UPDATE SET removed_at = NULL
                WHERE t_trading_items.removed_at IS NOT NULL
        ''', (account_id, row["id"], row["ts_code"], row["name"],
              abs(row["price"]), abs(row["amount"]), row["date"], side))

        conn.commit()

        # 查询插入或已存在的条目
        c.execute('SELECT * FROM t_trading_items WHERE account_id = ? AND transaction_id = ? AND trade_side = ?',
                  (account_id, req.transaction_id, side))
        item = c.fetchone()

    return {
        "status": "success",
        "imported": result.rowcount > 0,
        "data": {
            "id": item["id"],
            "ts_code": item["ts_code"],
            "name": item["name"],
            "trade_side": item["trade_side"],
            "base_price": item["buy_price"],
            "base_quantity": item["buy_quantity"],
            "base_date": item["buy_date"],
        }
    }


# ── DELETE: 软删除单条 ──

@router.delete("/t-trading/{item_id}")
def delete_t_trading_item(item_id: int):
    """软删除做T观察清单条目（设 removed_at，不物理删除）"""
    with get_db() as conn:
        c = conn.cursor()

        c.execute('SELECT id FROM t_trading_items WHERE id = ?', (item_id,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="T-trading item not found")

        removed_at = sh_timestamp_str()
        c.execute('UPDATE t_trading_items SET removed_at = ? WHERE id = ?',
                  (removed_at, item_id))
        conn.commit()

    return {"status": "success", "message": "Item removed from T-trading list"}


# ── DELETE: 清空账户做T记录（支持按 side 清空）──

@router.delete("/t-trading/{account_id}/clear")
def clear_t_trading_items(account_id: str, side: Optional[str] = Query(None)):
    """物理清空该账户做T记录。传 side=BUY|SELL 只清对应侧；不传则清空全部。"""
    # 严格校验 side 参数：非法值返回 400，仅 None 时清空全部
    if side is not None and side not in ('BUY', 'SELL'):
        raise HTTPException(status_code=400, detail="side must be 'BUY' or 'SELL'")

    with get_db() as conn:
        c = conn.cursor()
        if side is not None:
            c.execute('DELETE FROM t_trading_items WHERE account_id = ? AND trade_side = ?',
                      (account_id, side))
        else:
            c.execute('DELETE FROM t_trading_items WHERE account_id = ?', (account_id,))
        deleted = c.rowcount
        conn.commit()

    return {
        "status": "success",
        "deleted": deleted,
        "message": f"已清空 {deleted} 条做T记录"
    }
