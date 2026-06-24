# -*- coding: utf-8 -*-
"""
Stock K-Line & Valuation APIs — reads from market.db
Extracted from market.py for cleaner module separation.
"""
from fastapi import APIRouter
import logging

from web.backend.database import get_market_db

logger = logging.getLogger(__name__)
router = APIRouter()


_market_monthly_initialized = False


def _ensure_market_monthly():
    """Lazily ensure market_monthly table exists in market.db on first use."""
    global _market_monthly_initialized
    if _market_monthly_initialized:
        return
    with get_market_db() as conn:
        if conn is None:
            return
        conn.execute('''
        CREATE TABLE IF NOT EXISTS market_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year_month TEXT NOT NULL UNIQUE,
            total_mv_high REAL,
            total_mv_low REAL,
            pb_high REAL,
            pb_low REAL,
            pb_close REAL,
            net_assets REAL,
            trade_date TEXT,
            updated_at TEXT
        )
        ''')
        conn.commit()
    _market_monthly_initialized = True


@router.get("/stocks/kline/{ts_code}")
def get_stock_kline(ts_code: str, days: int = 252):
    """Get OHLCV K-line data from market.db daily_price table"""
    ts_code = ts_code.upper().strip()
    
    with get_market_db() as conn:
        if conn is None:
            return {"status": "error", "message": "market.db not found"}

        c = conn.cursor()
        c.execute('''
            SELECT trade_date, open, high, low, close, volume, amount
            FROM daily_price
            WHERE ts_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
        ''', (ts_code, days))
        rows = c.fetchall()

    if not rows:
        return {"status": "warning", "message": f"No K-line data for {ts_code}", "data": []}

    data = []
    for row in reversed(rows):
        data.append({
            "date": row["trade_date"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        })

    return {"status": "success", "data": data, "count": len(data)}


@router.get("/stocks/valuation/{ts_code}")
def get_stock_valuation(ts_code: str, days: int = 252):
    """Get PB/PE valuation history — prefer daily_valuation, fallback daily_snapshot"""
    ts_code = ts_code.upper().strip()
    
    with get_market_db() as conn:
        if conn is None:
            return {"status": "error", "message": "market.db not found"}

        c = conn.cursor()

        # Try daily_valuation first
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_valuation'")
        if c.fetchone():
            c.execute('''
                SELECT v.trade_date, v.pb, v.pe_ttm, p.close
                FROM daily_valuation v
                LEFT JOIN daily_price p ON v.ts_code = p.ts_code AND v.trade_date = p.trade_date
                WHERE v.ts_code = ?
                ORDER BY v.trade_date DESC
                LIMIT ?
            ''', (ts_code, days))
            rows = c.fetchall()

            if rows:
                data = [{"date": r["trade_date"], "pb": r["pb"], "pe": r["pe_ttm"], "close": r["close"]} for r in reversed(rows)]
                return {"status": "success", "data": data, "count": len(data)}

        # Fallback: daily_snapshot
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_snapshot'")
        if c.fetchone():
            c.execute('''
                SELECT trade_date, pb, pe_ttm, latest AS close
                FROM daily_snapshot
                WHERE ts_code = ?
                ORDER BY trade_date DESC
                LIMIT ?
            ''', (ts_code, days))
            rows = c.fetchall()

            if rows:
                data = [{"date": r["trade_date"], "pb": r["pb"], "pe": r["pe_ttm"], "close": r["close"]} for r in reversed(rows)]
                return {"status": "success", "data": data, "count": len(data)}

    return {"status": "warning", "message": f"No valuation data for {ts_code}", "data": []}
