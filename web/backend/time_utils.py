# -*- coding: utf-8 -*-
"""Shared business-time helpers for the fixed Asia/Shanghai project timezone."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

SH_TZ = ZoneInfo("Asia/Shanghai")


def sh_now() -> datetime:
    return datetime.now(SH_TZ)


def sh_today() -> date:
    return sh_now().date()


def sh_date_str() -> str:
    return sh_now().strftime("%Y-%m-%d")


def sh_timestamp_str() -> str:
    return sh_now().strftime("%Y-%m-%d %H:%M:%S")


def sh_compact_timestamp() -> str:
    return sh_now().strftime("%Y%m%d_%H%M%S")


def sh_batch_id() -> str:
    return sh_now().strftime("%Y%m%d-%H%M%S")


def sh_iso_str() -> str:
    return sh_now().isoformat()
