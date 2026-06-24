# -*- coding: utf-8 -*-
"""
IPO Calendar API — 股票打新通知
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.backend.database import get_db
from web.backend.time_utils import sh_date_str, sh_today

router = APIRouter()


class SyncIPORequest(BaseModel):
    days: int = 7
    start: Optional[str] = None
    end: Optional[str] = None


def _parse_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            from datetime import datetime
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise HTTPException(status_code=400, detail=f"无效日期格式: {value}")


def _get_calendar(start_date: date, end_date: date) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT market, board, ts_code, sub_code, name, subscribe_date,
                   issue_price, apply_limit, lottery_date, listing_date, source, updated_at
            FROM ipo_calendar
            WHERE subscribe_date >= ? AND subscribe_date <= ?
            ORDER BY subscribe_date, market, ts_code
            """,
            (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
        ).fetchall()

    return [dict(row) for row in rows]


@router.get("/ipo/calendar")
def get_ipo_calendar(days: int = 7, start: Optional[str] = None, end: Optional[str] = None):
    start_date = _parse_date(start) if start else sh_today()
    end_date = _parse_date(end) if end else (start_date + timedelta(days=days))
    items = _get_calendar(start_date, end_date)

    today_str = sh_date_str()
    by_market: dict[str, int] = {}
    by_date: dict[str, int] = {}
    last_updated = None
    for item in items:
        by_market[item["market"]] = by_market.get(item["market"], 0) + 1
        by_date[item["subscribe_date"]] = by_date.get(item["subscribe_date"], 0) + 1
        updated_at = item.get("updated_at")
        if updated_at and (last_updated is None or updated_at > last_updated):
            last_updated = updated_at

    return {
        "status": "success",
        "data": {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "today_count": sum(1 for item in items if item["subscribe_date"] == today_str),
            "total_count": len(items),
            "by_market": by_market,
            "by_date": by_date,
            "last_updated": last_updated,
            "items": items,
        },
    }


@router.get("/ipo/today")
def get_ipo_today():
    today = sh_today()
    items = _get_calendar(today, today)
    return {"status": "success", "data": items}


@router.post("/ipo/sync")
def sync_ipo_calendar_api(body: SyncIPORequest):
    start_date = _parse_date(body.start) if body.start else sh_today()
    end_date = _parse_date(body.end) if body.end else (start_date + timedelta(days=body.days))

    try:
        from scripts.etl.sync_ipo_calendar import sync_ipo_calendar
        result = sync_ipo_calendar(start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"同步 IPO 日历失败: {e}")

    return {"status": "success", "data": result, "message": "IPO 日历已同步"}
