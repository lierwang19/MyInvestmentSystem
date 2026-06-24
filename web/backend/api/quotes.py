# -*- coding: utf-8 -*-
"""
Real-time quote service.

Architecture:
- Watchlist: auto-built from holdings + high-score stocks
- Poller: background thread fetches quotes every N seconds during trading hours
- Cache: in-memory dict, no DB needed
- QuoteManager: multi-source fallback (tencent → sina → efinance)
- API: FastAPI endpoints for frontend consumption
"""

import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from fastapi import APIRouter

from web.backend.config import PROJECT_ROOT
from web.backend.services.quote_manager import quote_manager

logger = logging.getLogger("quotes")
logger.setLevel(logging.INFO)

# Shanghai timezone (UTC+8) — critical for Docker containers which default to UTC
CST = timezone(timedelta(hours=8))

router = APIRouter()

# ──────────────────────────────────────────
# In-memory state
# ──────────────────────────────────────────
_quote_cache: Dict[str, dict] = {}          # ts_code -> {price, change, ...}
_watchlist: List[str] = []                   # ["600519.SH", "000001.SZ", ...]
_watchlist_names: Dict[str, str] = {}        # ts_code -> name
_poller_thread: Optional[threading.Thread] = None
_poller_running = False
_last_fetch_time: Optional[str] = None
_poll_interval = 8  # seconds between fetches


# ──────────────────────────────────────────
# Watchlist Builder
# ──────────────────────────────────────────

def is_hk_trading_hours() -> bool:
    """Check if within HK market trading hours (9:25-12:05, 12:55-16:05 HKT=CST, trading days only)"""
    from web.backend.services.trading_calendar import is_hk_trading_day
    now = datetime.now(CST)
    if not is_hk_trading_day(now.date()):
        return False
    t = now.hour * 100 + now.minute
    if 925 <= t <= 1205:
        return True
    if 1255 <= t <= 1605:
        return True
    return False


def build_watchlist() -> List[str]:
    """Build watchlist from: (1) all holdings, (2) stocks with score >= 80"""
    global _watchlist, _watchlist_names

    codes = set()
    names = {}

    # Source 1: Holdings from all accounts
    try:
        from web.backend.database import get_db
        with get_db() as conn:
            c = conn.cursor()

            # Get all holding stock codes
            c.execute("SELECT DISTINCT ts_code, name FROM holdings WHERE quantity > 0")
            for row in c.fetchall():
                ts_code = row['ts_code']
                codes.add(ts_code)
                names[ts_code] = row['name'] or ''

        logger.info(f"Watchlist: {len(codes)} stocks from holdings")
    except Exception as e:
        logger.warning(f"Failed to load holdings for watchlist: {e}")

    # Source 2: High-score stocks from stock_pool
    try:
        from web.backend.database import get_db
        with get_db() as conn:
            c = conn.cursor()

            c.execute("SELECT ts_code, name FROM stock_pool WHERE score >= 80")
            pool_count = 0
            for row in c.fetchall():
                ts_code = row['ts_code']
                codes.add(ts_code)
                names[ts_code] = row['name'] or ''
                pool_count += 1

        logger.info(f"Watchlist: {pool_count} stocks from stock_pool (score>=80)")
    except Exception as e:
        logger.warning(f"Failed to load stock_pool for watchlist: {e}")

    _watchlist = sorted(codes)
    _watchlist_names = names
    logger.info(f"Watchlist total: {len(_watchlist)} unique stocks")
    return _watchlist


# ──────────────────────────────────────────
# Trading Hours Check
# ──────────────────────────────────────────

def is_trading_hours() -> bool:
    """Check if currently within A-share trading hours (9:15-11:35, 12:55-15:05, trading days only)"""
    from web.backend.services.trading_calendar import is_a_trading_day
    now = datetime.now(CST)

    # Trading day check (handles weekends + public holidays)
    if not is_a_trading_day(now.date()):
        return False

    t = now.hour * 100 + now.minute

    # Morning session: 9:15 - 11:35 (include pre-open and buffer)
    if 915 <= t <= 1135:
        return True
    # Afternoon session: 12:55 - 15:05 (include pre-open and buffer)
    if 1255 <= t <= 1505:
        return True

    return False


# ──────────────────────────────────────────
# Background Poller
# ──────────────────────────────────────────
def _poll_loop():
    """Background loop that fetches quotes during trading hours"""
    global _quote_cache, _poller_running, _last_fetch_time

    logger.info("Quote poller thread started")

    while _poller_running:
        try:
            a_codes  = [c for c in _watchlist if not c.endswith('.HK')]
            hk_codes = [c for c in _watchlist if c.endswith('.HK')]
            fetched = 0

            # A-share quotes (only during A-share trading hours)
            if a_codes and is_trading_hours():
                quotes = quote_manager.fetch_a(a_codes)
                if quotes:
                    _quote_cache.update(quotes)
                    fetched += len(quotes)

            # HK quotes (only during HK trading hours)
            if hk_codes and is_hk_trading_hours():
                hk_quotes = quote_manager.fetch_hk(hk_codes)
                if hk_quotes:
                    _quote_cache.update(hk_quotes)
                    fetched += len(hk_quotes)

            if fetched:
                _last_fetch_time = datetime.now(CST).strftime('%H:%M:%S')
                logger.debug(f"Fetched {fetched} quotes at {_last_fetch_time}")

            in_any_session = (a_codes and is_trading_hours()) or (hk_codes and is_hk_trading_hours())
            time.sleep(_poll_interval if in_any_session else 30)
        except Exception as e:
            logger.error(f"Poller error: {e}")
            time.sleep(10)

    logger.info("Quote poller thread stopped")


def start_poller():
    """Start the background quote polling thread"""
    global _poller_thread, _poller_running

    if _poller_running:
        logger.info("Poller already running")
        return

    # Build watchlist first
    build_watchlist()

    if not _watchlist:
        logger.warning("Watchlist is empty, poller not started")
        return

    _poller_running = True
    _poller_thread = threading.Thread(target=_poll_loop, daemon=True, name="quote-poller")
    _poller_thread.start()
    logger.info(f"Quote poller started: {len(_watchlist)} stocks, interval={_poll_interval}s")

    # Startup fetch: get quotes immediately so cache is never empty
    # (Tencent/Sina return last close price even outside trading hours)
    def _startup_fetch():
        global _quote_cache, _last_fetch_time
        try:
            all_quotes = quote_manager.fetch(_watchlist)
            if all_quotes:
                _quote_cache.update(all_quotes)
                _last_fetch_time = datetime.now(CST).strftime('%H:%M:%S')
                logger.info(f"Startup fetch: {len(all_quotes)} quotes cached")
        except Exception as e:
            logger.error(f"Startup fetch error: {e}")

    threading.Thread(target=_startup_fetch, daemon=True, name="startup-fetch").start()


def stop_poller():
    """Stop the background polling thread"""
    global _poller_running
    _poller_running = False
    logger.info("Quote poller stop requested")


# ──────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────
@router.get("/quotes/realtime")
def get_realtime_quotes(codes: str = ""):
    """
    Get real-time quotes.
    - No params: return all cached quotes
    - codes=600519.SH,000001.SZ: return specific stocks
      (缓存优先；缺失代码会即时补抓一次并回填缓存)
    """
    if codes:
        code_list = [c.strip() for c in codes.split(',') if c.strip()]
        result = {c: _quote_cache.get(c) for c in code_list if c in _quote_cache}

        missing = [c for c in code_list if c not in result]
        if missing:
            try:
                fetched = quote_manager.fetch(missing)
                if fetched:
                    _quote_cache.update(fetched)
                    result.update(fetched)
            except Exception as e:
                logger.warning(f"On-demand quote fetch failed for {missing[:5]}{'...' if len(missing) > 5 else ''}: {e}")
    else:
        result = dict(_quote_cache)

    return {
        "status": "success",
        "data": result,
        "count": len(result),
        "last_fetch": _last_fetch_time,
        "watchlist_size": len(_watchlist),
        "is_trading": is_trading_hours() or is_hk_trading_hours(),
        "is_a_trading": is_trading_hours(),
        "is_hk_trading": is_hk_trading_hours(),
        "poller_running": _poller_running,
    }


@router.get("/quotes/watchlist")
def get_watchlist():
    """Get current watchlist"""
    items = []
    for ts_code in _watchlist:
        items.append({
            "ts_code": ts_code,
            "name": _watchlist_names.get(ts_code, ''),
            "has_quote": ts_code in _quote_cache,
        })
    return {
        "status": "success",
        "data": items,
        "count": len(items),
    }


def _fetch_all_quotes() -> dict:
    """一次性拉取 watchlist 中全部股票（A 股 + 港股）的行情，走 quote_manager 自动 fallback。"""
    return quote_manager.fetch(_watchlist)


@router.post("/quotes/refresh-watchlist")
def refresh_watchlist():
    """Rebuild the watchlist from current holdings + stock pool"""
    build_watchlist()

    # If poller not running, do a one-shot fetch (A + HK)
    if not _poller_running and _watchlist:
        quotes = _fetch_all_quotes()
        if quotes:
            _quote_cache.update(quotes)

    return {
        "status": "success",
        "message": f"Watchlist refreshed: {len(_watchlist)} stocks",
        "count": len(_watchlist),
    }


@router.post("/quotes/fetch-now")
def fetch_now():
    """Force an immediate quote fetch (even outside trading hours), including HK stocks"""
    global _last_fetch_time

    if not _watchlist:
        build_watchlist()

    if not _watchlist:
        return {"status": "warning", "message": "Watchlist is empty"}

    quotes = _fetch_all_quotes()

    if quotes:
        _quote_cache.update(quotes)
        _last_fetch_time = datetime.now(CST).strftime('%H:%M:%S')

    return {
        "status": "success",
        "data": dict(_quote_cache),
        "count": len(quotes),
        "last_fetch": _last_fetch_time,
    }


@router.get("/quotes/status")
def get_poller_status():
    """Get poller status"""
    return {
        "poller_running": _poller_running,
        "watchlist_size": len(_watchlist),
        "cache_size": len(_quote_cache),
        "last_fetch": _last_fetch_time,
        "is_trading": is_trading_hours() or is_hk_trading_hours(),
        "is_a_trading": is_trading_hours(),
        "is_hk_trading": is_hk_trading_hours(),
        "poll_interval": _poll_interval,
    }


@router.post("/quotes/start")
def api_start_poller():
    """Start the real-time poller"""
    start_poller()
    return {"status": "success", "message": f"Poller started with {len(_watchlist)} stocks"}


@router.post("/quotes/stop")
def api_stop_poller():
    """Stop the real-time poller"""
    stop_poller()
    return {"status": "success", "message": "Poller stopped"}


@router.get("/quotes/sources")
def get_source_status():
    """Return quote_manager status: each fetcher's failure count and cooldown state"""
    return {
        "status": "success",
        "data": quote_manager.get_status(),
    }
