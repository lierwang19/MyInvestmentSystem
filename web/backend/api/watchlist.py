# -*- coding: utf-8 -*-
"""
Watchlist API — 自选股跟踪
全局自选股列表（不绑定账户），展示行情/估值/打分/各账户行业持仓比例。
一期只支持 A 股（.SH/.SZ）。
"""
import logging
import math
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.backend.database import get_db
from web.backend.time_utils import sh_date_str, sh_timestamp_str

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic Models ──

class AddWatchlistRequest(BaseModel):
    ts_code: str
    name: str = ""
    note: str = ""


class UpdateWatchlistRequest(BaseModel):
    note: Optional[str] = None
    sort_order: Optional[int] = None


# ── Helpers ──

def _valid_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _get_account_l1_weights() -> tuple:
    """
    返回:
    - accounts: [{account_id, account_name}]，按 name 排序
    - l1_weights: {account_id -> {l1_industry_name -> weight_pct (float)}}
      (weight_pct 已乘以 100，单位 %)

    使用 get_industry_weight_map 轻量函数，不调用 analyze_holdings。
    """
    from web.backend.api.analysis import get_industry_weight_map

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name FROM accounts ORDER BY name"
        ).fetchall()
    accounts = [{"account_id": r["id"], "account_name": r["name"]} for r in rows]

    l1_weights: dict = {}
    for acc in accounts:
        acc_id = acc["account_id"]
        wmap = get_industry_weight_map(acc_id)
        # 提取 L1 行业名 -> 权重 %（同一 L1 在 wmap 中多条记录值相同，取第一个）
        l1_seen: dict = {}
        for _code, info in wmap.items():
            l1 = info.get("industry_l1", "")
            if l1 and l1 not in l1_seen:
                w = _valid_float(info.get("industry_l1_actual"))
                l1_seen[l1] = round(w * 100, 2) if w is not None else None
        l1_weights[acc_id] = l1_seen

    return accounts, l1_weights


# ── GET: 自选股列表（含丰富数据） ──

@router.get("/watchlist")
def get_watchlist():
    """
    返回所有活跃自选股，含行情/估值/打分/各账户行业权重。

    顶层结构：
    {
      "status": "success",
      "accounts": [{account_id, account_name}],   ← 前端动态列头
      "data": [{...每条自选股数据...}]
    }
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist_stocks WHERE removed_at IS NULL ORDER BY sort_order, id"
        ).fetchall()
        removed_count = conn.execute(
            "SELECT COUNT(*) FROM watchlist_stocks WHERE removed_at IS NOT NULL"
        ).fetchone()[0]

    if not rows:
        # 空列表：只查账户列表用于列头，跳过 _get_account_l1_weights()
        with get_db() as conn:
            acc_rows = conn.execute("SELECT id, name FROM accounts ORDER BY name").fetchall()
        accounts = [{"account_id": r["id"], "account_name": r["name"]} for r in acc_rows]
        return {"status": "success", "accounts": accounts, "data": [], "removed_count": removed_count}

    # 有自选股时才执行账户行业权重聚合
    accounts, l1_weights = _get_account_l1_weights()

    ts_codes = [r["ts_code"] for r in rows]

    # 行情 + 估值 + 行业（复用 analysis.py 的已有函数）
    from web.backend.api.analysis import _load_market_data_from_db
    market_df = _load_market_data_from_db(ts_codes)
    market_map: dict = {}
    if not market_df.empty:
        for _, mrow in market_df.iterrows():
            market_map[str(mrow["ts_code"])] = mrow.to_dict()

    # 大数得分（直接读 stock_pool，同持仓分析口径）
    scores: dict = {}
    placeholders = ",".join("?" * len(ts_codes))
    with get_db() as conn:
        for sr in conn.execute(
            f"SELECT ts_code, score FROM stock_pool WHERE ts_code IN ({placeholders})",
            ts_codes
        ).fetchall():
            scores[sr["ts_code"]] = _valid_float(sr["score"])

    # 组装
    data = []
    for row in rows:
        code = row["ts_code"]
        m = market_map.get(code, {})

        latest = _valid_float(m.get("latest"))
        added_price = _valid_float(row["added_price"])
        change_pct = None
        if latest is not None and added_price and added_price > 0:
            change_pct = round((latest - added_price) / added_price * 100, 2)

        industry_l1 = str(m.get("tdx_l1", "") or "")

        # 各账户该 L1 行业权重（None = 空仓，前端显示 —）
        account_weights: dict = {}
        for acc in accounts:
            acc_id = acc["account_id"]
            if industry_l1:
                w = l1_weights.get(acc_id, {}).get(industry_l1)
                account_weights[acc_id] = w  # float 或 None
            else:
                account_weights[acc_id] = None

        data.append({
            "id":          row["id"],
            "ts_code":     code,
            "name":        row["name"] or code,
            "added_date":  row["added_date"],
            "added_price": added_price,
            "note":        row["note"] or "",
            "sort_order":  row["sort_order"] or 0,
            # 行情
            "latest":                 latest,
            "change_pct":             change_pct,
            "rise_from_1y_low_pct":   _valid_float(m.get("rise_from_1y_low_pct")),
            "rise_from_3y_low_pct":   _valid_float(m.get("rise_from_3y_low_pct")),
            "rise_from_all_low_pct":  _valid_float(m.get("rise_from_all_low_pct")),
            "low_252d":  _valid_float(m.get("low_252d")),
            "low_756d":  _valid_float(m.get("low_756d")),
            "low_all":   _valid_float(m.get("low_all")),
            # 估值
            "pb":     _valid_float(m.get("pb")),
            "pe_ttm": _valid_float(m.get("pe_ttm")),
            # 打分（stock_pool，同持仓分析口径）
            "score": scores.get(code),
            # 行业
            "industry_l1": industry_l1,
            "industry":    str(m.get("tdx_l2", "") or ""),
            # 各账户权重 dict
            "account_weights": account_weights,
        })

    return {"status": "success", "accounts": accounts, "data": data, "removed_count": removed_count}


# ── DELETE: 清空已软删记录（注意：必须放在 /{item_id} 之前，避免路由冲突） ──

@router.delete("/watchlist/clear-removed")
def clear_removed_watchlist():
    """物理清空所有已软删的自选股记录"""
    with get_db() as conn:
        conn.execute("DELETE FROM watchlist_stocks WHERE removed_at IS NOT NULL")
        deleted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
    return {"status": "success", "deleted": deleted, "message": f"已清空 {deleted} 条已删除记录"}


# ── POST: 添加自选股 ──

@router.post("/watchlist")
def add_watchlist_item(req: AddWatchlistRequest):
    """
    添加股票到自选列表。
    - 一期只支持 A 股（.SH/.SZ）
    - added_price 为添加时的最新收盘价快照，持久化后不再变
    - 软删后重新添加 = 复活旧记录并刷新 added_date/added_price
    """
    ts_code = req.ts_code.strip().upper()

    if not (ts_code.endswith(".SH") or ts_code.endswith(".SZ")):
        raise HTTPException(
            status_code=400,
            detail="暂不支持港股，目前仅支持 A 股（.SH / .SZ）"
        )

    # 获取最新收盘价作为 added_price 快照
    added_price = None
    try:
        from web.backend.database import get_market_db
        with get_market_db() as mconn:
            if mconn is not None:
                r = mconn.execute(
                    "SELECT close FROM daily_price WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 1",
                    (ts_code,)
                ).fetchone()
                if r:
                    added_price = float(r[0])
    except Exception as e:
        logger.warning(f"[watchlist] 获取 {ts_code} 收盘价失败: {e}")

    today = sh_date_str()

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, removed_at FROM watchlist_stocks WHERE ts_code = ?",
            (ts_code,)
        ).fetchone()

        if existing:
            if existing["removed_at"] is None:
                raise HTTPException(status_code=409, detail="该股票已在自选列表中")
            # 软删后重新添加：复活记录
            conn.execute(
                """
                UPDATE watchlist_stocks
                SET removed_at = NULL,
                    added_date = ?,
                    added_price = ?,
                    note = ?,
                    name = CASE WHEN ? != '' THEN ? ELSE name END
                WHERE ts_code = ?
                """,
                (today, added_price, req.note, req.name, req.name, ts_code)
            )
        else:
            conn.execute(
                """
                INSERT INTO watchlist_stocks (ts_code, name, added_price, added_date, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts_code, req.name or ts_code, added_price, today, req.note)
            )
        conn.commit()

        item = conn.execute(
            "SELECT * FROM watchlist_stocks WHERE ts_code = ?", (ts_code,)
        ).fetchone()

    return {
        "status": "success",
        "data": {
            "id":          item["id"],
            "ts_code":     item["ts_code"],
            "name":        item["name"],
            "added_date":  item["added_date"],
            "added_price": item["added_price"],
        }
    }


# ── PUT: 更新备注/排序 ──

@router.put("/watchlist/{item_id}")
def update_watchlist_item(item_id: int, req: UpdateWatchlistRequest):
    """更新自选股的备注或排序权重"""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM watchlist_stocks WHERE id = ? AND removed_at IS NULL",
            (item_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="自选股记录不存在")

        if req.note is not None:
            conn.execute(
                "UPDATE watchlist_stocks SET note = ? WHERE id = ?",
                (req.note, item_id)
            )
        if req.sort_order is not None:
            conn.execute(
                "UPDATE watchlist_stocks SET sort_order = ? WHERE id = ?",
                (req.sort_order, item_id)
            )
        conn.commit()

    return {"status": "success"}


# ── DELETE: 软删除单条 ──

@router.delete("/watchlist/{item_id}")
def delete_watchlist_item(item_id: int):
    """软删除自选股（设 removed_at，不物理删除）"""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM watchlist_stocks WHERE id = ? AND removed_at IS NULL",
            (item_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="自选股记录不存在")

        conn.execute(
            "UPDATE watchlist_stocks SET removed_at = ? WHERE id = ?",
            (sh_timestamp_str(), item_id)
        )
        conn.commit()

    return {"status": "success", "message": "已从自选列表移除"}
