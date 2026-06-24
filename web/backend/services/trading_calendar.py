# -*- coding: utf-8 -*-
"""
交易日历工具 — 基于 exchange-calendars 判断各市场交易日

支持：
  - A 股（XSHG，上交所日历，同步适用深交所）
  - 港股（XHKG）

如果 exchange-calendars 未安装或初始化失败，自动降级为"仅周末过滤"模式，
行为与原 is_trading_hours() 一致，不影响基本功能。

使用方式：
    from web.backend.services.trading_calendar import is_a_trading_day, is_hk_trading_day

    if not is_a_trading_day():
        return  # 今日非交易日，跳过

依赖：pip install exchange-calendars>=4.0,<5.0
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))

try:
    import exchange_calendars as xcals
    _XCALS_AVAILABLE = True
    logger.info("[trading_calendar] exchange-calendars 已加载")
except ImportError:
    xcals = None  # type: ignore[assignment]
    _XCALS_AVAILABLE = False
    logger.warning("[trading_calendar] exchange-calendars 未安装，降级为周末过滤模式（节假日不检测）")


# 日期级缓存：避免每次轮询都查询日历（key: "XSHG:2026-03-25"）
_cache: dict = {}


def _today_cst() -> date:
    return datetime.now(CST).date()


def is_trading_day(market_code: str, dt: Optional[date] = None) -> bool:
    """
    判断给定日期是否为指定市场交易日。

    Args:
        market_code: 'XSHG'（A 股上交所）或 'XHKG'（港交所）
        dt: 日期，默认当天（CST）

    Returns:
        True = 交易日，False = 非交易日（含周末、节假日）
    """
    if dt is None:
        dt = _today_cst()

    cache_key = f"{market_code}:{dt}"
    if cache_key in _cache:
        return _cache[cache_key]

    if not _XCALS_AVAILABLE:
        # 降级：只过滤周末，不检测节假日
        result = dt.weekday() < 5
        _cache[cache_key] = result
        return result

    try:
        cal = xcals.get_calendar(market_code)
        result = bool(cal.is_session(str(dt)))
        _cache[cache_key] = result
        return result
    except Exception as e:
        logger.warning(f"[trading_calendar] {market_code} 日历查询失败: {e}，降级为周末过滤")
        result = dt.weekday() < 5
        _cache[cache_key] = result
        return result


def is_a_trading_day(dt: Optional[date] = None) -> bool:
    """当天是否为 A 股交易日（含节假日检测）"""
    return is_trading_day('XSHG', dt)


def is_hk_trading_day(dt: Optional[date] = None) -> bool:
    """当天是否为港股交易日（含节假日检测）"""
    return is_trading_day('XHKG', dt)
