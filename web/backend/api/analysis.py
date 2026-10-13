# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException
import os
import math
import pandas as pd
import sys
import numpy as np
from copy import deepcopy

# Add project root to sys.path
from web.backend.config import PROJECT_ROOT, MARKET_DB_PATH, USER_DB_PATH

try:
    import build_portfolio
except ModuleNotFoundError:
    scoring_dir = os.path.join(PROJECT_ROOT, "scripts", "scoring")
    if scoring_dir not in sys.path:
        sys.path.append(scoring_dir)
    import build_portfolio
from web.backend.api.upload import UPLOAD_DIR
from web.backend.utils import normalize_code, valid_float, dict_factory
from concurrent.futures import ThreadPoolExecutor
import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

_INDUSTRY_BASE_CACHE = {
    "mtime": None,
    "map": {},
}

_SCORING_CONFIG_CACHE = {
    "mtime": None,
    "config": None,
}

_ALL_L1_INDUSTRIES_CACHE = {
    "mtime": None,
    "items": [],
}

_HOLDINGS_RESPONSE_CACHE = {}


def _safe_mtime(path: str):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _db_cache_mtime(path: str):
    """数据库缓存键用的时间戳。

    WAL 模式下事务提交可能只追加 -wal，主库文件 mtime 不变；
    把 -wal/-shm 的 mtime 一并取最大，避免缓存长期不失效。
    """
    return max((_safe_mtime(p) or 0) for p in (path, path + "-wal", path + "-shm"))


def _get_latest_holdings_file_mtime(account_id: str):
    acc_dir = os.path.join(UPLOAD_DIR, "holdings", account_id)
    if not os.path.isdir(acc_dir):
        return None
    try:
        files = sorted(os.listdir(acc_dir), reverse=True)
    except OSError:
        return None
    if not files:
        return None
    return _safe_mtime(os.path.join(acc_dir, files[0]))


def _get_holdings_cache_key(account_id: str):
    return (
        account_id,
        _db_cache_mtime(USER_DB_PATH),
        _db_cache_mtime(MARKET_DB_PATH),
        _safe_mtime(os.path.join(PROJECT_ROOT, "config", "scoring.yaml")),
        _safe_mtime(os.path.join(PROJECT_ROOT, "stock_industry_base.csv")),
        _get_latest_holdings_file_mtime(account_id),
    )


def _get_industry_base_map() -> dict:
    """按文件 mtime 缓存 stock_industry_base.csv，避免每次请求都重读 5000+ 行。"""
    base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    if not os.path.exists(base_path):
        return {}

    try:
        mtime = os.path.getmtime(base_path)
    except OSError:
        return {}

    if _INDUSTRY_BASE_CACHE["mtime"] == mtime and _INDUSTRY_BASE_CACHE["map"]:
        return _INDUSTRY_BASE_CACHE["map"]

    ind_map: dict = {}
    try:
        df_base = pd.read_csv(base_path, dtype=str)
        for _, r in df_base.iterrows():
            code = r.get("ts_code", "")
            if not code:
                continue
            ind_map[code] = {
                "tdx_l1": r.get("tdx_l1", "") or "",
                "tdx_l2": r.get("tdx_l2", "") or "",
                "anchor_industry": r.get("anchor_industry", "") or r.get("tdx_l2", "") or "",
                "name": r.get("name", "") or "",
            }
    except Exception:
        return {}

    _INDUSTRY_BASE_CACHE["mtime"] = mtime
    _INDUSTRY_BASE_CACHE["map"] = ind_map
    return ind_map


def _get_scoring_config():
    """按文件 mtime 缓存 scoring.yaml，避免持仓页每次都重新解析 YAML。"""
    config_path = os.path.join(PROJECT_ROOT, "config", "scoring.yaml")
    try:
        mtime = os.path.getmtime(config_path)
    except OSError:
        return build_portfolio.load_yaml(config_path)

    cached = _SCORING_CONFIG_CACHE
    if cached["mtime"] == mtime and cached["config"] is not None:
        return cached["config"]

    cfg = build_portfolio.load_yaml(config_path)
    cached["mtime"] = mtime
    cached["config"] = cfg
    return cfg


def _get_all_l1_industries() -> list[str]:
    base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    try:
        mtime = os.path.getmtime(base_path)
    except OSError:
        return []

    cached = _ALL_L1_INDUSTRIES_CACHE
    if cached["mtime"] == mtime and cached["items"]:
        return cached["items"]

    items: list[str] = []
    try:
        df_base = pd.read_csv(base_path, dtype=str, encoding="utf-8-sig")
        items = [x for x in df_base["tdx_l1"].dropna().unique().tolist() if str(x).strip()]
    except Exception:
        return []

    cached["mtime"] = mtime
    cached["items"] = items
    return items


def _resolve_market_ts_code(ts_code: str) -> str:
    """
    将持仓代码映射为 market.db 中更可能存在的行情代码。

    港股通持仓可能保存 WR 代码，例如：
      81810.HK -> 01810.HK
      83690.HK -> 03690.HK
      89988.HK -> 09988.HK
    """
    code = str(ts_code or "").strip().upper()
    if not code.endswith(".HK"):
        return code
    plain = code.split(".")[0]
    if len(plain) == 5 and plain[0] in ("8", "9"):
        return f"0{plain[1:]}.HK"
    return code


def _is_hk_wr_code(ts_code: str) -> bool:
    code = str(ts_code or "").strip().upper()
    return code.endswith(".HK") and _resolve_market_ts_code(code) != code


def _default_hk_industry_info(ts_code: str) -> dict:
    code = str(ts_code or "").strip().upper()
    if code.endswith(".HK"):
        return {
            "tdx_l1": "港股",
            "tdx_l2": "港股",
            "anchor_industry": "港股",
        }
    return {}


def _calendar_window_start_expr(years: int) -> str:
    """
    生成 SQLite 日期表达式：
    以全市场最新交易日为区间终点，按自然日回溯 N 年，起点采用开区间：
      latest_date = 2026-04-16 -> 1 年窗口从 2025-04-17 开始
    """
    return f"date((SELECT MAX(trade_date) FROM daily_price), '-{years} year', '+1 day')"


def _load_market_data_from_db(ts_codes: list) -> pd.DataFrame:
    """从 market.db 直接查询市场数据，替代 market_enriched.csv。
    返回列：ts_code, tdx_l1, tdx_l2, anchor_industry,
             pb, pe_ttm, latest, low_252d, rise_from_1y_low_pct
    行业三级体系（同 incon.dat）：
      tdx_l1 = 大类（T01: 能源/材料/...）
      tdx_l2 = 中类（T0101: 银行/煤炭/...）  ← 用于锚定PB评分
      anchor_industry = tdx_l2 经别名标准化后的名称
    """
    from web.backend.database import get_market_db

    if not ts_codes:
        return pd.DataFrame(columns=[
            "ts_code", "tdx_l1", "tdx_l2", "anchor_industry",
            "pb", "pe_ttm", "latest", "low_252d", "rise_from_1y_low_pct",
            "low_756d", "rise_from_3y_low_pct", "low_all", "rise_from_all_low_pct"
        ])

    # 1. 行业数据 — 从 stock_industry_base.csv（tdx_l1/l2/anchor_industry）
    ind_map = _get_industry_base_map()  # ts_code -> {tdx_l1, tdx_l2, anchor_industry}

    # 2. 价格与近 1/3 年自然日低点 + PB/PE — 从 market.db
    resolved_by_original = {code: _resolve_market_ts_code(code) for code in ts_codes}
    query_codes = list(dict.fromkeys(resolved_by_original.values()))
    price_data: dict = {}
    pb_pe_data: dict = {}
    placeholders = ",".join("?" * len(query_codes))

    with get_market_db() as conn:
        if conn is not None:
            # 最新收盘价 + 按自然日窗口计算的 1/3 年低点
            try:
                one_year_start_expr = _calendar_window_start_expr(1)
                three_year_start_expr = _calendar_window_start_expr(3)
                value_rows = ",".join(["(?)"] * len(query_codes))
                rows = conn.execute(f"""
                    WITH codes(ts_code) AS (VALUES {value_rows})
                    SELECT c.ts_code,
                           (
                               SELECT close
                               FROM daily_price
                               WHERE ts_code = c.ts_code
                               ORDER BY trade_date DESC
                               LIMIT 1
                           ) AS latest,
                           (
                               SELECT MIN(low)
                               FROM daily_price
                               WHERE ts_code = c.ts_code
                                 AND trade_date >= {one_year_start_expr}
                           ) AS low_252d,
                           (
                               SELECT MIN(low)
                               FROM daily_price
                               WHERE ts_code = c.ts_code
                                 AND trade_date >= {three_year_start_expr}
                           ) AS low_756d,
                           (
                               SELECT MIN(low)
                               FROM daily_price
                               WHERE ts_code = c.ts_code
                           ) AS low_all
                    FROM codes c
                """, query_codes).fetchall()
                for r in rows:
                    price_data[r[0]] = {
                        "latest":   r[1] or 0.0,
                        "low_252d": r[2] or 0.0,
                        "low_756d": r[3] or 0.0,
                        "low_all":  r[4] or 0.0,
                    }
            except Exception as e:
                logger.warning(f"market.db price query failed: {e}")

            # PB/PE — 优先 daily_valuation（估算当前值），回退 daily_snapshot
            # 估算公式：bvps = val_close / val_pb → est_pb = current_close / bvps
            try:
                rows = conn.execute(f"""
                    WITH latest_dv AS (
                        -- 每只股票取 trade_date 最大的那一行（原 GROUP BY + HAVING 写法
                        -- 在 SQLite 中对 bare column 取值未定义，不保证拿到最新行）
                        SELECT dv.ts_code, dv.pb, dv.pe_ttm, dv.trade_date
                        FROM daily_valuation dv
                        INNER JOIN (
                            SELECT ts_code, MAX(trade_date) AS max_date
                            FROM daily_valuation
                            WHERE ts_code IN ({placeholders})
                            GROUP BY ts_code
                        ) m ON dv.ts_code = m.ts_code AND dv.trade_date = m.max_date
                    )
                    SELECT lv.ts_code, lv.pb, lv.pe_ttm, lv.trade_date, dp.close AS val_close
                    FROM latest_dv lv
                    LEFT JOIN daily_price dp
                           ON lv.ts_code = dp.ts_code AND lv.trade_date = dp.trade_date
                """, query_codes).fetchall()
                for r in rows:
                    ts, val_pb, val_pe, val_date, val_close = r[0], r[1], r[2], r[3], r[4]
                    cur = price_data.get(ts, {}).get("latest")
                    if val_pb and val_close and cur and val_close > 0:
                        pb_pe_data[ts] = {
                            "pb":     round(cur * val_pb / val_close, 3),
                            "pe_ttm": round(cur * val_pe / val_close, 2) if val_pe else None,
                            "pb_source": "est",  # 标记为估算值
                        }
                    elif val_pb:
                        pb_pe_data[ts] = {"pb": round(val_pb, 3), "pe_ttm": round(val_pe, 2) if val_pe else None, "pb_source": "val"}
            except Exception as e:
                logger.warning(f"daily_valuation PB/PE query failed: {e}")

            # 对 daily_valuation 中没有数据的股票，回退 daily_snapshot
            missing = [c for c in query_codes if c not in pb_pe_data]
            if missing:
                mp = ",".join("?" * len(missing))
                try:
                    rows = conn.execute(f"""
                        SELECT ds.ts_code, ds.pb, ds.pe_ttm
                        FROM daily_snapshot ds
                        WHERE ds.ts_code IN ({mp})
                          AND ds.trade_date = (
                              SELECT MAX(trade_date) FROM daily_snapshot WHERE ts_code = ds.ts_code
                          )
                    """, missing).fetchall()
                    for r in rows:
                        if r[1]:  # pb not null/zero
                            pb_pe_data[r[0]] = {"pb": r[1], "pe_ttm": r[2], "pb_source": "snap"}
                except Exception:
                    pass

    # 3. 组装 DataFrame
    records = []
    for ts_code in ts_codes:
        market_code = resolved_by_original.get(ts_code, ts_code)
        p = price_data.get(market_code, {})
        latest_for_range = p.get("latest", 0.0)
        latest   = latest_for_range
        low_252d = p.get("low_252d", 0.0)
        low_756d = p.get("low_756d", 0.0)
        low_all  = p.get("low_all",  0.0)
        rise_1y  = round((latest_for_range - low_252d) / low_252d * 100, 2) if low_252d > 0 else 0.0
        rise_3y  = round((latest_for_range - low_756d) / low_756d * 100, 2) if low_756d > 0 else 0.0
        rise_all = round((latest_for_range - low_all)  / low_all  * 100, 2) if low_all  > 0 else 0.0
        vp = pb_pe_data.get(market_code, {})
        ind = ind_map.get(ts_code, {})
        if not ind:
            ind = _default_hk_industry_info(ts_code)
        records.append({
            "ts_code": ts_code,
            "name": ind.get("name", "") or "",  # 最新股票名称，供 analyze_holdings 更名同步用
            "tdx_l1": ind.get("tdx_l1", "") or "Unknown",
            "tdx_l2": ind.get("tdx_l2", "") or "Unknown",
            "anchor_industry": ind.get("anchor_industry", "") or "",
            "pb": vp.get("pb") or None,
            "pe_ttm": vp.get("pe_ttm") or None,
            "pb_source": vp.get("pb_source"),
            "latest":   latest,
            "low_252d": low_252d,
            "low_756d": low_756d,
            "low_all":  low_all,
            "rise_from_1y_low_pct":  rise_1y,
            "rise_from_3y_low_pct":  rise_3y,
            "rise_from_all_low_pct": rise_all,
        })
    return pd.DataFrame(records)


def get_industry_weight_map(account_id: str) -> dict:
    """
    轻量行业权重聚合，与 analyze_holdings 口径一致。

    计算链：holdings × market.db latest_close → industry MV 汇总
            分母 = total_holding_MV + cash_balance（同 analyze_holdings）
    不跑打分、不读 PB/PE、不查风险标记。

    返回 {ts_code -> {industry_l1, industry, industry_l1_actual, industry_actual}}
    失败时返回空 dict，不影响调用方渲染。
    """
    import csv as _csv
    from web.backend.database import get_db, get_market_db

    # 1. 持仓 + 现金
    holdings: dict = {}
    cash_balance = 0.0
    with get_db() as conn:
        for r in conn.execute(
            "SELECT ts_code, quantity FROM holdings WHERE account_id = ? AND quantity > 0",
            (account_id,)
        ).fetchall():
            holdings[str(r["ts_code"])] = float(r["quantity"] or 0)
        row = conn.execute(
            "SELECT cash_balance FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if row:
            cash_balance = float(row["cash_balance"] or 0)

    if not holdings:
        return {}

    # 2. 行业映射（stock_industry_base.csv）
    ind_map: dict = {}
    base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    try:
        with open(base_path, 'r', encoding='utf-8-sig') as f:
            for r in _csv.DictReader(f):
                code = r.get('ts_code', '').strip()
                if code:
                    ind_map[code] = {
                        'industry_l1': r.get('tdx_l1', '') or '',
                        'industry':    r.get('tdx_l2', '') or '',
                    }
    except Exception:
        pass

    # 3. 最新收盘价（market.db，仅查持仓股票）
    # WR→底层代码映射，与 _load_market_data_from_db() 保持一致
    prices: dict = {}
    all_codes = list(holdings.keys())
    resolved_by_original = {code: _resolve_market_ts_code(code) for code in all_codes}
    query_codes = list(dict.fromkeys(resolved_by_original.values()))
    placeholders = ','.join('?' * len(query_codes))
    try:
        with get_market_db() as mconn:
            if mconn is not None:
                rows = mconn.execute(f"""
                    WITH latest AS (
                        SELECT ts_code, MAX(trade_date) AS max_date
                        FROM daily_price WHERE ts_code IN ({placeholders})
                        GROUP BY ts_code
                    )
                    SELECT dp.ts_code, dp.close
                    FROM daily_price dp
                    JOIN latest l ON dp.ts_code = l.ts_code AND dp.trade_date = l.max_date
                """, query_codes).fetchall()
                # 将查询结果映射回原始持仓代码
                market_prices = {str(r[0]): float(r[1] or 0) for r in rows}
                for orig_code in all_codes:
                    mkt_code = resolved_by_original.get(orig_code, orig_code)
                    if mkt_code in market_prices:
                        prices[orig_code] = market_prices[mkt_code]
    except Exception as e:
        logger.warning(f"[get_industry_weight_map] market.db 查询失败: {e}")

    # 4. 按行业汇总市值
    l1_mv: dict = {}
    l2_mv: dict = {}
    total_mv = 0.0
    for code, qty in holdings.items():
        price = prices.get(code, 0)
        if price <= 0:
            continue
        mv = price * qty
        total_mv += mv
        # 行业映射：WR 代码可能在 ind_map 中没有，尝试用底层代码查找
        ind = ind_map.get(code, {})
        if not ind:
            resolved_code = resolved_by_original.get(code, code)
            if resolved_code != code:
                ind = ind_map.get(resolved_code, {})
        if not ind:
            ind = {
                'industry_l1': _default_hk_industry_info(code).get("tdx_l1", ""),
                'industry': _default_hk_industry_info(code).get("tdx_l2", ""),
            }
        l1 = ind.get('industry_l1', '')
        l2 = ind.get('industry', '')
        if l1:
            l1_mv[l1] = l1_mv.get(l1, 0) + mv
        if l2:
            l2_mv[l2] = l2_mv.get(l2, 0) + mv

    total_assets = total_mv + cash_balance
    if total_assets <= 0:
        return {}

    # 5. 输出（含行业名称，供调用方直接使用）
    result: dict = {}
    for code in all_codes:
        ind = ind_map.get(code, {})
        if not ind:
            resolved_code = resolved_by_original.get(code, code)
            if resolved_code != code:
                ind = ind_map.get(resolved_code, {})
        if not ind:
            ind = {
                'industry_l1': _default_hk_industry_info(code).get("tdx_l1", ""),
                'industry': _default_hk_industry_info(code).get("tdx_l2", ""),
            }
        l1 = ind.get('industry_l1', '')
        l2 = ind.get('industry', '')
        result[code] = {
            'industry_l1':        l1,
            'industry':           l2,
            'industry_l1_actual': round(l1_mv.get(l1, 0) / total_assets, 4) if l1 else 0.0,
            'industry_actual':    round(l2_mv.get(l2, 0) / total_assets, 4) if l2 else 0.0,
        }
    return result


@router.get("/analysis/holdings/{account_id}")
def analyze_holdings(account_id: str):
    """Analyze holdings with scoring and real-time prices"""
    cache_key = _get_holdings_cache_key(account_id)
    cached = _HOLDINGS_RESPONSE_CACHE.get(account_id)
    if cached and cached["key"] == cache_key:
        return deepcopy(cached["value"])

    from web.backend.database import get_db

    # 1. Load Holdings Data - Priority: Database first, then files
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT ts_code, name, quantity, cost_price, total_cost 
            FROM holdings WHERE account_id = ?
        ''', (account_id,))
        db_holdings = c.fetchall()
    
    if db_holdings:
        # Use database holdings
        df_holdings = pd.DataFrame([dict(row) for row in db_holdings])
        # Rename columns to match expected format
        df_holdings = df_holdings.rename(columns={
            'quantity': 'Quantity',
            'cost_price': 'Cost',
            'total_cost': 'TotalCost'
        })

        # ── 名称清洗：除权期间统一加 XD/DR/SD 前缀，对用户不友好 ──
        # 对于持仓名称含 XD/DR/SD/N 前缀的，从 transactions 里找该股票最近一条
        # 不含前缀的 name 作为显示名称（只影响前端展示，不修改 holdings 表）
        EX_PREFIXES = ("XD", "DR", "SD", "N ", "XR")
        dirty_codes = [
            row["ts_code"] for _, row in df_holdings.iterrows()
            if isinstance(row.get("name"), str)
            and any(row["name"].startswith(p) for p in EX_PREFIXES)
        ]
        if dirty_codes:
            with get_db() as conn2:
                c2 = conn2.cursor()
                placeholders = ",".join("?" * len(dirty_codes))
                # 取每个 ts_code 中最新的非除权名称
                clean_names = c2.execute(f"""
                    SELECT ts_code, name
                    FROM transactions
                    WHERE ts_code IN ({placeholders})
                      AND name IS NOT NULL
                      AND name NOT LIKE 'XD%'
                      AND name NOT LIKE 'DR%'
                      AND name NOT LIKE 'SD%'
                      AND name NOT LIKE 'XR%'
                    GROUP BY ts_code
                    HAVING date = MAX(date)
                """, dirty_codes).fetchall()
                clean_map = {r["ts_code"]: r["name"] for r in clean_names}
            # 更新 df_holdings 中的名称
            def _clean_name(row):
                if any(str(row.get("name","")).startswith(p) for p in EX_PREFIXES):
                    return clean_map.get(row["ts_code"], row.get("name", ""))
                return row.get("name", "")
            df_holdings["name"] = df_holdings.apply(_clean_name, axis=1)

        use_db = True
    else:
        # Fall back to file-based holdings
        acc_dir = os.path.join(UPLOAD_DIR, "holdings", account_id)
        if not os.path.exists(acc_dir):
            return {"status": "warning", "message": "No holdings uploaded for this account"}
        
        files = os.listdir(acc_dir)
        files.sort(reverse=True)
        if not files:
            return {"status": "warning", "message": "No holdings file found"}
        
        holdings_path = os.path.join(acc_dir, files[0])
        
        try:
            if holdings_path.endswith(".csv"):
                df_holdings = pd.read_csv(holdings_path, dtype=str)
            else:
                df_holdings = pd.read_excel(holdings_path, dtype=str)
        except Exception as e:
            logger.error(f"Error reading holdings {holdings_path}: {e}")
            return {"status": "error", "message": f"Failed to read holdings: {str(e)}"}
        use_db = False

    # 3. Identify Columns
    if use_db:
        # Database holdings already have standard columns
        code_col = "ts_code"
        name_col = "name"
        qty_col = "Quantity"
        cost_col = "Cost"
        mv_col = None  # Will be calculated from price * quantity
        price_col = None
        profit_col = None
        pct_col = None
        growth_col = None
    else:
        # File-based holdings need heuristic column detection
        cols = df_holdings.columns
        
        def find_col(keywords):
            for k in keywords:
                for c in cols:
                    if k in c: return c
            return None

        code_col = find_col(["证券代码", "代码", "Code", "ts_code"])
        name_col = find_col(["证券名称", "名称", "Name"])
        mv_col = find_col(["参考市值", "市值", "MarketValue", "Assets"])
        qty_col = find_col(["证券数量", "数量", "股份余额", "Quantity", "Amount"])
        cost_col = find_col(["成本价", "Cost"])
        price_col = find_col(["市价", "最新价", "Price"])
        profit_col = find_col(["盈亏金额", "盈亏", "Profit"])
        pct_col = find_col(["盈亏比例", "盈亏比", "Pct", "Ratio"])
        growth_col = find_col(["今年涨幅", "年涨幅", "涨幅", "Change", "Yearly"])

        if not code_col:
            logger.error(f"Failed to identify columns. Available: {cols.tolist()}")
            return {"status": "error", "message": f"Could not identify Stock Code column. Available: {cols.tolist()}"}
        
        # Normalize codes for file-based holdings
        df_holdings["ts_code"] = df_holdings[code_col].apply(normalize_code)

    # 3. Load Market Data from DB for these specific holdings
    ts_codes = df_holdings["ts_code"].dropna().unique().tolist()
    df_market = _load_market_data_from_db(ts_codes)

    # 3b. 可转债补丁：market.db 无可转债数据，用实时行情补名称+现价
    _BOND_PREFIXES = ("110", "113", "123", "127", "128")
    bond_codes = [c for c in ts_codes if c.split(".")[0][:3] in _BOND_PREFIXES]
    if bond_codes:
        mkt_with_price = set(df_market.loc[df_market["latest"] > 0, "ts_code"])
        missing_bonds = [c for c in bond_codes if c not in mkt_with_price]
        if missing_bonds:
            try:
                from web.backend.services.quote_manager import quote_manager
                bond_quotes = quote_manager.fetch(missing_bonds)
                for code, q in (bond_quotes or {}).items():
                    mask = df_market["ts_code"] == code
                    if mask.any():
                        if q.get("price", 0) > 0:
                            df_market.loc[mask, "latest"] = q["price"]
                        if q.get("name"):
                            df_market.loc[mask, "name"] = q["name"]
            except Exception as e:
                logger.warning(f"可转债实时行情补充失败: {e}")

    # 4. Merge
    merged = df_holdings.merge(df_market, on="ts_code", how="left", suffixes=("", "_mkt"))
    
    # Load Config
    cfg = _get_scoring_config()
    pos_cfg = cfg.get("positives", {})
    
    results = []
    
    total_assets = 0
    if mv_col:
        merged[mv_col] = pd.to_numeric(merged[mv_col], errors="coerce").fillna(0)
        total_assets = merged[mv_col].sum()

    for row in merged.to_dict(orient="records"):
        # Basic Info
        code = row["ts_code"]
        _held_name = row[name_col] if name_col else row.get("name", "Unknown")
        # 优先用 stock_industry_base.csv 里的最新名称（股票更名后能自动同步）
        # merge 时 df_market.name → name_mkt（df_holdings.name 保留为 name）
        _csv_name = row.get("name_mkt", "") or ""
        name = _csv_name if _csv_name else (_held_name or "Unknown")
        # tdx_l1 = 大类（能源/材料/金融...），tdx_l2 = 中类（银行/煤炭...）
        tdx_l1 = row.get("tdx_l1", "") or "Unknown"
        tdx_l2 = row.get("tdx_l2", "") or "Unknown"
        if pd.isna(tdx_l1): tdx_l1 = "Unknown"
        if pd.isna(tdx_l2): tdx_l2 = "Unknown"
        if str(code).upper().endswith(".HK") and (tdx_l1 == "Unknown" or tdx_l2 == "Unknown"):
            tdx_l1 = "港股"
            tdx_l2 = "港股"
        # anchor_industry 是评分锚定行业（中类名称经别名标准化），用于锚定PB查找
        anchor_industry = row.get("anchor_industry", "") or tdx_l2
        if pd.isna(anchor_industry) or not anchor_industry: anchor_industry = tdx_l2
        # 分组/展示用行业 = 二级行业（中类），更有意义；一级行业作为补充
        industry = tdx_l2
        
        # Valuation (from Market Data)
        # Market data likely uses Chinese cols too? Let's check df_market cols if needed
        # Assuming cleaned market data has std cols 'pb', 'pe_ttm' IF generated by system
        # BUT if market data is RAW upload, we need to map there too.
        # Let's map row values flexibly
        
        pb = pd.to_numeric(row.get("pb", row.get("市净率", 0)), errors="coerce")
        pe = pd.to_numeric(row.get("pe_ttm", row.get("市盈(TTM)", 0)), errors="coerce")
        
        # Holdings Extra Info
        cost = pd.to_numeric(row.get(cost_col, 0), errors="coerce") if cost_col else 0
        quantity = pd.to_numeric(row.get(qty_col, 0), errors="coerce") if qty_col else 0
        
        # Price: ONLY use system price (from market_enriched.csv latest column)
        # If unavailable, return 0 so user knows the data fetch failed
        price = 0.0
        if "latest" in row:
            system_price = pd.to_numeric(row["latest"], errors="coerce")
            if valid_float(system_price) > 0:
                price = valid_float(system_price)
        
        # Original profit from Excel (for first-time import)
        profit_from_excel = pd.to_numeric(row.get(profit_col, 0), errors="coerce") if profit_col else 0
        profit_pct_from_excel = pd.to_numeric(row.get(pct_col, 0), errors="coerce") if pct_col else 0
        
        # Real-time profit calculation (if price is available)
        # Profit = (Price - Cost) * Quantity
        # Profit % = (Price - Cost) / Cost * 100
        # Real-time profit calculation (if price is available)
        # Profit = (Price - Cost) * Quantity
        if price > 0:
            profit = (price - valid_float(cost)) * valid_float(quantity)
            
            # Profit Pct calculation
            if valid_float(cost) > 0:
                profit_pct = ((price - valid_float(cost)) / valid_float(cost)) * 100
            else:
                # If cost is <= 0 (Recovered), Profit% is technically infinite/undefined
                # We return 0 here and let UI handle "Cost Recovered" display
                profit_pct = 0
        else:
            # Price unavailable, fallback to Excel
            profit = profit_from_excel
            profit_pct = profit_pct_from_excel
        
        # Mark if cost has been recovered (for UI display)
        cost_recovered = valid_float(cost) <= 0
        
        # Yearly Growth Priority: System (rise_from_1y_low_pct) > Holdings
        # 这里的 1 年 / 3 年低点已改为“自然日窗口”定义，而非 252 / 756 交易日
        growth_val = 0
        if "rise_from_1y_low_pct" in row:
             growth_val = pd.to_numeric(row["rise_from_1y_low_pct"], errors="coerce")
        elif "yearly_growth" in row:
             growth_val = pd.to_numeric(row["yearly_growth"], errors="coerce")
             
        # Extract low values (参考价格，口径与 market.db daily_price 保持一致)
        low_val     = pd.to_numeric(row.get("low_252d", 0), errors="coerce") or 0
        low_756d_val = pd.to_numeric(row.get("low_756d", 0), errors="coerce") or 0
        low_all_val  = pd.to_numeric(row.get("low_all",  0), errors="coerce") or 0
        rise_3y_val  = pd.to_numeric(row.get("rise_from_3y_low_pct",  0), errors="coerce")
        rise_all_val = pd.to_numeric(row.get("rise_from_all_low_pct", 0), errors="coerce")
        
        # If system growth is NaN or 0 (and we suspect it shouldn't be), we might still want it if it's valid 0.
        # But usually growth is non-zero. Let's just check if it's not NaN.
        if pd.notna(growth_val) and growth_val != 0:
             pass # Use system growth
        elif growth_col:
             # Fallback to holdings only if system failed
             growth_val = pd.to_numeric(row.get(growth_col, 0), errors="coerce")
        
        # Market Value: Calculate from Quantity × Current Price (real-time)
        if price > 0 and valid_float(quantity) > 0:
            mv = valid_float(quantity) * price
        else:
            # Fallback to Excel market value if real-time calculation not possible
            mv = pd.to_numeric(row.get(mv_col, 0), errors="coerce") if mv_col else 0
            mv = valid_float(mv)
        
        # Scoring Logic — 用 anchor_industry（中类标准名）查锚定PB
        anchor = 2.5 # Default
        win_prob = 0.5
        ideal_ratio_raw = 0.0

        if anchor_industry and anchor_industry != "Unknown" and pd.notna(pb) and pb > 0:
            anchor = build_portfolio.get_anchor(anchor_industry, pos_cfg)
            win_prob = build_portfolio.calc_win_prob(pb, anchor)
            ideal_ratio_raw = build_portfolio.calc_allocation_ratio(win_prob)
            
        ind_cap = 0.03
        target_weight = ind_cap * ideal_ratio_raw
        
        results.append({
            "ts_code": code,
            "name": name,
            "industry": industry,         # 二级行业（中类）用于分组和展示
            "industry_l1": tdx_l1,        # 一级行业（大类）
            "anchor_industry": anchor_industry,
            "sw_industry": industry,  # Alias for frontend compatibility
            "market_value": valid_float(mv),
            "actual_weight": 0,  # Will be calculated after all MVs are computed
            "target_weight": valid_float(round(target_weight, 4)),
            "pb": round(float(pb), 2) if pd.notna(pb) and pb > 0 else None,
            "pe": round(float(pe), 2) if pd.notna(pe) and pe > 0 else None,
            "pb_source": row.get("pb_source") if pd.notna(row.get("pb_source")) else None,
            "anchor_pb": valid_float(anchor),
            "win_prob": valid_float(win_prob),
            "score": valid_float(round(win_prob * 100, 1)),
            "suggestion": "HOLD",  # Will be recalculated after actual_weight is set
            # Holdings fields
            "quantity": valid_float(quantity),
            "cost": valid_float(round(cost, 3)),
            "cost_recovered": cost_recovered,  # NEW: Flag for "已回本" status
            "is_cleared": valid_float(quantity) == 0,  # NEW: Flag for cleared holdings
            "price": valid_float(round(price, 2)),
            "profit": valid_float(round(profit, 0)),
            "profit_pct": valid_float(round(profit_pct, 2)),
            "yearly_growth": valid_float(round(growth_val, 2)),
            "low_252d":  valid_float(round(low_val,      2)),  # 近1年最低价（口径跟随 daily_price）
            "low_756d":  valid_float(round(low_756d_val, 2)),  # 近3年最低价（口径跟随 daily_price）
            "low_all":   valid_float(round(low_all_val,  2)),  # 历史最低价（口径跟随 daily_price）
            "rise_from_3y_low_pct":  valid_float(round(rise_3y_val,  2)) if pd.notna(rise_3y_val)  else 0.0,
            "rise_from_all_low_pct": valid_float(round(rise_all_val, 2)) if pd.notna(rise_all_val) else 0.0,
        })
    
    # Calculate total_assets = 持仓市值 + 现金余额
    total_market_value = sum(r["market_value"] for r in results)
    cash_balance = 0.0
    try:
        with get_db() as _conn:
            row = _conn.execute(
                "SELECT cash_balance FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if row:
                cash_balance = float(row["cash_balance"] or 0)
    except Exception:
        pass
    total_assets = total_market_value + cash_balance
    
    # Update actual_weight and suggestion for each stock
    for r in results:
        if total_assets > 0:
            r["actual_weight"] = valid_float(round(r["market_value"] / total_assets, 4))
        else:
            r["actual_weight"] = 0
        
    SECTOR_CAP = 0.05 # 5% max per sector (User Request)
    if results:
        industry_actual_totals: dict[str, float] = {}
        industry_target_raw_totals: dict[str, float] = {}
        l1_actual_totals: dict[str, float] = {}
        l1_target_totals: dict[str, float] = {}

        for item in results:
            if total_assets > 0:
                item["actual_weight"] = valid_float(round(item["market_value"] / total_assets, 4))
            else:
                item["actual_weight"] = 0
            industry = item["industry"]
            industry_l1 = item["industry_l1"]
            industry_actual_totals[industry] = industry_actual_totals.get(industry, 0.0) + item["actual_weight"]
            industry_target_raw_totals[industry] = industry_target_raw_totals.get(industry, 0.0) + item["target_weight"]
            l1_actual_totals[industry_l1] = l1_actual_totals.get(industry_l1, 0.0) + item["actual_weight"]

        scale_factors: dict[str, float] = {}
        for industry, raw_total in industry_target_raw_totals.items():
            scale_factors[industry] = (SECTOR_CAP / raw_total) if raw_total > SECTOR_CAP else 1.0

        industry_target_totals: dict[str, float] = {}
        for item in results:
            industry = item["industry"]
            industry_l1 = item["industry_l1"]
            item["target_weight"] = item["target_weight"] * scale_factors.get(industry, 1.0)
            t = item["target_weight"]
            a = item["actual_weight"]
            rise = item.get("yearly_growth") or 0
            if rise >= 100:
                item["suggestion"] = f"SELL{int(rise // 100)}"
            elif t > a * 1.2:
                item["suggestion"] = "BUY"
            elif t < a * 0.8:
                item["suggestion"] = "SELL"
            else:
                item["suggestion"] = "HOLD"

            industry_target_totals[industry] = industry_target_totals.get(industry, 0.0) + item["target_weight"]
            l1_target_totals[industry_l1] = l1_target_totals.get(industry_l1, 0.0) + item["target_weight"]

        industry_distribution = [
            {
                "industry": industry,
                "actual_weight": valid_float(round(industry_actual_totals.get(industry, 0.0), 4)),
                "target_weight": valid_float(round(industry_target_totals.get(industry, 0.0), 4)),
            }
            for industry in sorted(industry_actual_totals.keys())
        ]
        ind_lookup = {
            row["industry"]: {"actual_weight": row["actual_weight"], "target_weight": row["target_weight"]}
            for row in industry_distribution
        }
        ind_l1_lookup = {
            industry: valid_float(round(weight, 4))
            for industry, weight in l1_actual_totals.items()
        }

        all_l1 = _get_all_l1_industries()
        if all_l1:
            l1_keys = all_l1
        else:
            l1_keys = sorted(set(l1_actual_totals.keys()) | set(l1_target_totals.keys()))
        l1_distribution = [
            {
                "industry": industry,
                "actual_weight": valid_float(round(l1_actual_totals.get(industry, 0.0), 4)),
                "target_weight": valid_float(round(l1_target_totals.get(industry, 0.0), 4)),
            }
            for industry in l1_keys
        ]

        final_results = []
        NULLABLE_FIELDS = {"pb", "pe", "pb_source"}
        for item in results:
            item["industry_actual"] = ind_lookup.get(item["industry"], {}).get("actual_weight", 0)
            item["industry_target"] = ind_lookup.get(item["industry"], {}).get("target_weight", 0)
            item["industry_l1_actual"] = ind_l1_lookup.get(item["industry_l1"], 0)
            for k, v in list(item.items()):
                if k in NULLABLE_FIELDS:
                    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                        item[k] = None
                elif isinstance(v, (float, int, np.float64, np.int64)):
                    item[k] = valid_float(v)
            final_results.append(item)

        final_results.sort(key=lambda x: (x["industry"], -x["win_prob"]))
    else:
        final_results = []
        industry_distribution = []
        l1_distribution = []

    # Enrich with risk flags if available
    try:
        from web.backend.database import get_db
        with get_db() as rconn:
            rc = rconn.cursor()
            rc.execute("SELECT ts_code, risk_level, ok_debt, ok_goodwill, ok_pledge, negative_count FROM risk_flags")
            risk_map = {row["ts_code"]: dict(row) for row in rc.fetchall()}

        for r in final_results:
            rf = risk_map.get(r["ts_code"], {})
            r["risk_level"] = rf.get("risk_level")
            r["ok_debt"] = rf.get("ok_debt", 1)
            r["ok_goodwill"] = rf.get("ok_goodwill", 1)
            r["ok_pledge"] = rf.get("ok_pledge", 1)
            r["negative_count"] = rf.get("negative_count", 0)
    except Exception:
        pass  # risk_flags table may not exist yet

    response = {
        "status": "success",
        "account_id": account_id,
        "total_assets": float(total_assets),
        "total_market_value": float(total_market_value),
        "cash_balance": float(cash_balance),
        "holdings": final_results,  # Changed from 'data' to 'holdings' for frontend
        "industry_stats": industry_distribution,
        "industry_l1_stats": l1_distribution
    }
    _HOLDINGS_RESPONSE_CACHE[account_id] = {
        "key": cache_key,
        "value": deepcopy(response),
    }
    return response

def process_history_file(filename):
    """Helper to extract valuation stats from a historical file"""
    try:
        path = os.path.join(UPLOAD_DIR, filename)
        parts = filename.split("_")
        if len(parts) < 2: return None
        date_str = parts[1]
        
        if filename.endswith(".csv"):
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)
            
        # Map Columns
        cols = df.columns
        def get_val(keys):
            for k in keys:
                for c in cols:
                    if k == c: return df[c] # Exact match first
            for k in keys:
                for c in cols:
                    if k in c: return df[c] # Partial
            return None

        s_pb = get_val(["pb", "市净率"])
        s_pe = get_val(["pe_ttm", "市盈(TTM)", "市盈率"])
        s_mv = get_val(["total_mv", "总市值"])
        
        if s_pb is None: return None
        
        # Clean Data (Coerce '--', space, etc to NaN)
        s_pb = pd.to_numeric(s_pb, errors='coerce')
        if s_pe is not None: s_pe = pd.to_numeric(s_pe, errors='coerce')
        if s_mv is not None: s_mv = pd.to_numeric(s_mv, errors='coerce')
        
        pb_median = s_pb.median(skipna=True) # skipna default is True but being explicit

        pe_median = s_pe.median() if s_pe is not None else 0
        total_cap = s_mv.sum() if s_mv is not None else 0
        
        return {
            "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
            "pb": valid_float(round(pb_median, 2)),
            "pe": valid_float(round(pe_median, 2)),
            "market_cap": valid_float(total_cap)
        }
    except Exception as e:
        logger.error(f"Error processing {filename}: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

@router.get("/analysis/market-overview")
def get_market_overview():
    # 1. Get List of Market Files
    files = [f for f in os.listdir(UPLOAD_DIR) if f.startswith("market_") and (f.endswith(".xlsx") or f.endswith(".csv"))]
    files.sort()
    
    if not files:
         return {"status": "warning", "message": "No market data uploaded"}

    # 3. Calculate Trend (Process all files)
    history = []
    # No ThreadPool for now to avoid context issues with simple script
    # Or keep it simple
    for f in files:
        r = process_history_file(f)
        if r: history.append(r)
            
    history.sort(key=lambda x: x["date"])
    
    if not history:
        return {"status": "warning", "message": "Failed to parse any market files"}

    latest = history[-1]
    
    return {
        "status": "success",
        "latest": {
            "date": latest["date"],
            "pb": latest["pb"],
            "pe": latest["pe"],
            "total_cap": latest["market_cap"],
        },
        "history": history
    }


# NOTE: Stock pool endpoints moved to stock_pool.py
# NOTE: Risk flags endpoints moved to risk.py

# NOTE: Stock pool endpoints moved to stock_pool.py
# NOTE: Risk flags endpoints moved to risk.py

@router.get("/analysis/dividends/{account_id}")
def analyze_dividends(account_id: str):
    """Aggregate dividend history by year/month and stock"""
    from web.backend.database import get_db
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT ts_code, name, total_amt, date 
            FROM transactions 
            WHERE account_id = ? AND trans_type = 'DIVIDEND'
            ORDER BY date DESC
        ''', (account_id,))
        rows = c.fetchall()
    
    # Aggregations
    monthly = {} # "YYYY-MM": amount
    yearly = {}  # "YYYY": amount
    by_stock = {} # "code": {name, amount}
    
    total_dividend = 0.0
    
    for row in rows:
        amount = row['total_amt']
        date = row['date'] # YYYY-MM-DD
        code = row['ts_code']
        name = row['name']
        
        # Monthly
        month_key = date[:7]
        monthly[month_key] = monthly.get(month_key, 0) + amount
        
        # Yearly
        year_key = date[:4]
        yearly[year_key] = yearly.get(year_key, 0) + amount
        
        # By Stock
        if code not in by_stock:
            by_stock[code] = {"name": name, "amount": 0.0, "code": code}
        by_stock[code]["amount"] += amount
        
        total_dividend += amount
        
    # Convert to lists for frontend
    monthly_list = [{"date": k, "amount": round(v, 2), "year": k[:4]} for k, v in monthly.items()]
    monthly_list.sort(key=lambda x: x["date"])
    
    yearly_list = [{"year": k, "amount": round(v, 2)} for k, v in yearly.items()]
    yearly_list.sort(key=lambda x: x["year"])
    
    stock_list = list(by_stock.values())
    stock_list.sort(key=lambda x: x["amount"], reverse=True)
    
    return {
        "status": "success",
        "data": {
            "total": round(total_dividend, 2),
            "monthly": monthly_list,
            "yearly": yearly_list,
            "by_stock": stock_list[:10] # Top 10 stats
        }
    }


@router.get("/analysis/account-overview")
def get_account_overview():
    """行业分类 × 账户 矩阵——所有账户的 L1/L2 行业持仓权重"""
    from web.backend.database import get_db

    # 1. 全部账户
    with get_db() as conn:
        accounts = [dict(r) for r in conn.execute(
            "SELECT id, name FROM accounts ORDER BY name"
        ).fetchall()]

    if not accounts:
        return {"accounts": [], "rows": []}

    # 2. 每个账户的行业权重（复用 get_industry_weight_map 保持口径一致）
    # wmap: {ts_code -> {industry_l1, industry, industry_l1_actual, industry_actual}}
    # 注意：同一 L1 下多只股票的 industry_l1_actual 相同，取 max 而不是 sum
    all_weights: dict = {}  # {account_id: {l1: {"_l1_weight": float, l2: float, ...}}}
    for acct in accounts:
        wmap = get_industry_weight_map(acct["id"])
        l1l2: dict = {}
        for _code, info in wmap.items():
            l1 = info.get("industry_l1") or ""
            l2 = info.get("industry") or ""
            l1_w = info.get("industry_l1_actual", 0)
            l2_w = info.get("industry_actual", 0)
            if not l1:
                continue
            if l1 not in l1l2:
                l1l2[l1] = {"_l1_weight": l1_w}
            else:
                # 同一 L1 下取 max（值本来就相同，防御性写法）
                l1l2[l1]["_l1_weight"] = max(l1l2[l1]["_l1_weight"], l1_w)
            if l2 and l2 not in l1l2[l1]:
                l1l2[l1][l2] = l2_w
            elif l2:
                l1l2[l1][l2] = max(l1l2[l1][l2], l2_w)
        all_weights[acct["id"]] = l1l2

    # 3. 收集所有 L1→L2 结构（全账户并集）
    all_l1l2: dict = {}
    for acct_weights in all_weights.values():
        for l1, l2_map in acct_weights.items():
            if l1 not in all_l1l2:
                all_l1l2[l1] = set()
            for l2 in l2_map:
                if l2 != "_l1_weight":
                    all_l1l2[l1].add(l2)

    # 4. 组装矩阵行
    rows = []
    for l1 in sorted(all_l1l2.keys()):
        # L1 汇总行
        weights: dict = {}
        for acct in accounts:
            w = all_weights.get(acct["id"], {}).get(l1, {}).get("_l1_weight")
            weights[acct["id"]] = round(w, 4) if w is not None else 0.0
        rows.append({
            "l1_industry": l1,
            "l2_industry": "",
            "row_type": "l1",
            "parent_l1": l1,
            "weights": weights,
        })
        # L2 明细行
        for l2 in sorted(all_l1l2[l1]):
            weights = {}
            for acct in accounts:
                w = all_weights.get(acct["id"], {}).get(l1, {}).get(l2)
                weights[acct["id"]] = round(w, 4) if w is not None else 0.0
            rows.append({
                "l1_industry": l1,
                "l2_industry": l2,
                "row_type": "l2",
                "parent_l1": l1,
                "weights": weights,
            })

    return {
        "accounts": [{"account_id": a["id"], "account_name": a["name"]} for a in accounts],
        "rows": rows,
    }
