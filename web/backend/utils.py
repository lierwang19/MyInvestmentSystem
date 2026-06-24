# -*- coding: utf-8 -*-
"""
Common utility functions shared across backend modules.
Consolidates duplicate code patterns found in multiple API files.
"""

import os
import math
import numpy as np

# ── Project paths ──
from web.backend.config import PROJECT_ROOT


def normalize_code(code: str) -> str:
    """
    Normalize stock code to tushare format (e.g., 000001.SZ, 600519.SH).
    Handles raw 6-digit codes and already-suffixed codes.
    """
    code = str(code).strip().zfill(6)
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith("8") or code.startswith("4"):
        return f"{code}.BJ"
    return f"{code}.SZ"


def valid_float(val, default=0.0) -> float:
    """Ensure value is a valid float for JSON serialization (no NaN/Inf)."""
    if val is None:
        return default
    if isinstance(val, (np.float64, np.int64)):
        val = float(val)
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def dict_factory(cursor, row) -> dict:
    """SQLite row factory that returns dict instead of tuple."""
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d
