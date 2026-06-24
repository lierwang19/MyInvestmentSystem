# -*- coding: utf-8 -*-
"""
HK Stocks API — 港股主档管理

数据来源：新浪财经（直连可用，约 2700 只港股）

Endpoints:
  GET  /hk-stocks/search?q=...  — 搜索港股（供 StockSearch 调用）
  POST /hk-stocks/sync          — 从新浪财经同步港股列表
  GET  /hk-stocks/list          — 分页列出所有港股
"""
import logging
import requests

from fastapi import APIRouter
from web.backend.database import get_db
from web.backend.time_utils import sh_iso_str

logger = logging.getLogger(__name__)
router = APIRouter()

_SINA_URL = (
    "https://vip.stock.finance.sina.com.cn"
    "/quotes_service/api/json_v2.php/Market_Center.getHKStockData"
)
_SINA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _fetch_hk_stocks_sina() -> list[tuple[str, str]]:
    """
    翻页拉取新浪财经港股列表，返回 [(ts_code, name), ...] 列表。
    每页 60 条，遇到空页停止。
    """
    result: dict[str, str] = {}  # ts_code → name，自动去重
    for page in range(1, 100):   # 上限 ~6000 条，实际约 2700 条 46 页
        params = {
            "page": str(page),
            "num": "60",
            "sort": "symbol",
            "asc": "1",
            "node": "qbgg_hk",
            "_s_r_a": "init",
        }
        resp = requests.get(_SINA_URL, headers=_SINA_HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        for item in data:
            code = str(item.get("symbol", "")).strip().zfill(5)
            name = str(item.get("name", "")).strip()
            if code and code != "00000" and name:
                ts_code = f"{code}.HK"
                result.setdefault(ts_code, name)

    return list(result.items())


@router.get("/hk-stocks/search")
def search_hk_stocks(q: str = ""):
    """搜索港股：按代码或名称模糊匹配，最多返回 20 条"""
    q = q.strip()
    if not q:
        return {"status": "success", "data": []}
    pattern = f"%{q}%"
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT ts_code, name, sector FROM hk_stocks "
            "WHERE ts_code LIKE ? OR name LIKE ? ORDER BY ts_code LIMIT 20",
            (pattern, pattern),
        )
        rows = c.fetchall()
    data = [
        {
            "ts_code": r["ts_code"],
            "name": r["name"] or "",
            "industry": r["sector"] or "港股",
            "value": r["ts_code"],
            "label": f"{r['ts_code']} {r['name'] or ''}",
        }
        for r in rows
    ]
    return {"status": "success", "data": data}


@router.post("/hk-stocks/sync")
def sync_hk_stocks():
    """
    从新浪财经拉取全量港股列表，写入 hk_stocks 表。
    约 2700 只（包含港股通全部标的）。
    代码格式：5 位数字 → XXXXX.HK。
    """
    try:
        records = _fetch_hk_stocks_sina()
    except Exception as e:
        logger.error(f"hk_stocks sync (sina) failed: {e}")
        return {"status": "error", "message": f"新浪财经接口调用失败: {e}"}

    if not records:
        return {"status": "error", "message": "新浪财经未返回数据，请检查网络"}

    synced_at = sh_iso_str()
    rows = [(ts_code, name, "港股", synced_at) for ts_code, name in records]

    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM hk_stocks")
        c.executemany(
            "INSERT INTO hk_stocks (ts_code, name, sector, synced_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()

    logger.info(f"hk_stocks synced (sina): {len(rows)} stocks")
    return {
        "status": "success",
        "message": f"已同步 {len(rows)} 只港股（含全部港股通标的）",
        "count": len(rows),
    }


@router.get("/hk-stocks/list")
def list_hk_stocks(page: int = 1, page_size: int = 50, q: str = ""):
    """分页列出港股主档"""
    offset = (page - 1) * page_size
    with get_db() as conn:
        c = conn.cursor()
        if q:
            pattern = f"%{q}%"
            c.execute(
                "SELECT COUNT(*) as cnt FROM hk_stocks WHERE ts_code LIKE ? OR name LIKE ?",
                (pattern, pattern),
            )
            total = c.fetchone()["cnt"]
            c.execute(
                "SELECT ts_code, name, sector, synced_at FROM hk_stocks "
                "WHERE ts_code LIKE ? OR name LIKE ? ORDER BY ts_code LIMIT ? OFFSET ?",
                (pattern, pattern, page_size, offset),
            )
        else:
            c.execute("SELECT COUNT(*) as cnt FROM hk_stocks")
            total = c.fetchone()["cnt"]
            c.execute(
                "SELECT ts_code, name, sector, synced_at FROM hk_stocks ORDER BY ts_code LIMIT ? OFFSET ?",
                (page_size, offset),
            )
        rows = c.fetchall()
    return {
        "status": "success",
        "data": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
