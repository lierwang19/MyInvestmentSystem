# -*- coding: utf-8 -*-
"""
Stock Pool API — manages the scored stock screening pool.
Extracted from analysis.py for cleaner module separation.
"""
from fastapi import APIRouter, HTTPException
import os
import sys
import csv
import shutil
import subprocess
import logging
from copy import deepcopy

from web.backend.config import PROJECT_ROOT
from web.backend.database import get_db
from web.backend.utils import dict_factory

logger = logging.getLogger(__name__)
router = APIRouter()

_L1_MAP_CACHE = {
    "mtime": None,
    "map": {},
}

_SCORING_PARAMS_CACHE = {
    "mtime": None,
    "params": None,
}

_STOCK_POOL_RESPONSE_CACHE = {
    "key": None,
    "value": None,
}


def _safe_mtime(path: str):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _get_stock_pool_cache_key():
    return (
        _safe_mtime(os.path.join(PROJECT_ROOT, "user_data.db")),
        _safe_mtime(os.path.join(PROJECT_ROOT, "market.db")),
        _safe_mtime(os.path.join(PROJECT_ROOT, "config", "scoring.yaml")),
        _safe_mtime(os.path.join(PROJECT_ROOT, "stock_industry_base.csv")),
    )


def _rebuild_enriched_csv(base_path: str, enriched_path: str):
    """
    调用 make_market_enriched_from_db.py 重建 market_enriched.csv。
    失败时抛 HTTPException，阻断后续评分（不静默降级）。
    先输出到临时文件，成功后原子替换，避免半写入。
    """
    script  = os.path.join(PROJECT_ROOT, "scripts", "etl", "make_market_enriched_from_db.py")
    db_path = os.path.join(PROJECT_ROOT, "market.db")

    if not os.path.isfile(db_path):
        raise HTTPException(status_code=400, detail="market.db 不存在，无法生成 market_enriched.csv")

    tmp_path = enriched_path + ".tmp"
    cmd = [
        sys.executable, script,
        "--base", base_path,
        "--db", db_path,
        "--out", tmp_path,
        "--use-tushare", "0",
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=PROJECT_ROOT)
        logger.info(f"market_enriched.csv 重建成功\n{result.stdout[-500:] if result.stdout else ''}")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or str(e))[-300:]
        logger.error(f"market_enriched.csv 重建失败: {err}")
        raise HTTPException(status_code=500, detail=f"market_enriched.csv 生成失败: {err}")

    if not os.path.isfile(tmp_path):
        raise HTTPException(status_code=500, detail="market_enriched.csv 临时文件未生成，脚本可能未正常输出")

    shutil.move(tmp_path, enriched_path)


def _load_scoring_params():
    """
    从 config/scoring.yaml 读取权重和距低点分桶配置。
    失败时 fallback 到与 YAML 默认值一致的硬编码常量。
    返回 (w_pb, w_pe, w_dist, buckets)。
    """
    import yaml
    config_path = os.path.join(PROJECT_ROOT, "config", "scoring.yaml")
    try:
        mtime = os.path.getmtime(config_path)
    except OSError:
        mtime = None
    if (
        mtime is not None
        and _SCORING_PARAMS_CACHE["mtime"] == mtime
        and _SCORING_PARAMS_CACHE["params"] is not None
    ):
        return _SCORING_PARAMS_CACHE["params"]
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        weights  = cfg.get("positives", {}).get("weights", {})
        w_pb     = float(weights.get("pb",                   0.4))
        w_pe     = float(weights.get("pe_ttm",               0.4))
        w_dist   = float(weights.get("distance_from_1y_low", 0.2))
        buckets  = (cfg.get("positives", {})
                       .get("distance_from_1y_low", {})
                       .get("buckets", []))
        params = (w_pb, w_pe, w_dist, buckets)
        if mtime is not None:
            _SCORING_PARAMS_CACHE["mtime"] = mtime
            _SCORING_PARAMS_CACHE["params"] = params
        return params
    except Exception as e:
        logger.warning(f"scoring.yaml 读取失败，使用默认权重/分桶: {e}")
        return 0.4, 0.4, 0.2, [
            {"max": 3,  "score": 100},
            {"max": 6,  "score": 90},
            {"max": 10, "score": 75},
            {"max": 20, "score": 50},
        ]


def _get_l1_map() -> dict:
    base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    try:
        mtime = os.path.getmtime(base_path)
    except OSError:
        return {}

    if _L1_MAP_CACHE["mtime"] == mtime and _L1_MAP_CACHE["map"]:
        return _L1_MAP_CACHE["map"]

    l1_map: dict = {}
    try:
        with open(base_path, "r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code = (r.get("ts_code") or "").strip()
                if code:
                    l1_map[code] = (r.get("tdx_l1") or "").strip()
    except Exception as e:
        logger.warning(f"stock_industry_base.csv 读取失败: {e}")
        return {}

    _L1_MAP_CACHE["mtime"] = mtime
    _L1_MAP_CACHE["map"] = l1_map
    return l1_map


def _apply_dist_buckets(rise_pct, buckets) -> "int | None":
    """
    按 scoring.yaml distance_from_1y_low.buckets 分桶规则计算得分。
    用于 1年/3年 低点涨幅评分，复用同一规则。
    """
    if rise_pct is None:
        return None
    for b in buckets:
        if "max" in b and rise_pct <= b["max"]:
            return int(b["score"])
    # 没有命中任何 max 桶，取 else 桶或 fallback 25
    for b in buckets:
        if "else" in b:
            return int(b["else"])
    return 25


def _calendar_window_start_expr(years: int) -> str:
    """
    生成 SQLite 日期表达式：
    以全市场最新交易日为区间终点，按自然日回溯 N 年，起点采用开区间。
    """
    return f"date((SELECT MAX(trade_date) FROM daily_price), '-{years} year', '+1 day')"


def _enrich_pool_rows(rows: list) -> list:
    """
    给 stock_pool 的行补充市场数据：
    - tdx_l1 (一级行业，来自 stock_industry_base.csv)
    - latest (最新收盘价)
    - pct_chg (当日涨跌幅 %)
    - low_252d / low_756d / low_all (近1年/3年/历史最低价，按自然日窗口)
    - rise_from_1y_low_pct / rise_from_3y_low_pct / rise_from_all_low_pct
    """
    if not rows:
        return rows

    # 1. 读取一级行业映射
    l1_map = _get_l1_map()

    # 2. 从 market.db 查价格数据
    ts_codes = [r["ts_code"] for r in rows if r.get("ts_code")]
    price_map: dict = {}

    if ts_codes:
        try:
            from web.backend.database import get_market_db
            placeholders = ",".join("?" * len(ts_codes))
            with get_market_db() as conn:
                if conn is not None:
                    # daily_price 无 pct_chg 列，用今日/昨日收盘价计算
                    one_year_start_expr = _calendar_window_start_expr(1)
                    three_year_start_expr = _calendar_window_start_expr(3)
                    value_rows = ",".join(["(?)"] * len(ts_codes))
                    db_rows = conn.execute(f"""
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
                                   SELECT close
                                   FROM daily_price
                                   WHERE ts_code = c.ts_code
                                   ORDER BY trade_date DESC
                                   LIMIT 1 OFFSET 1
                               ) AS prev_close,
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
                    """, ts_codes).fetchall()
                    for r in db_rows:
                        ts, lat, prev, l252, l756, lall = r
                        lat   = float(lat  or 0)
                        prev  = float(prev or 0)
                        l252  = float(l252 or 0)
                        l756  = float(l756 or 0)
                        lall  = float(lall or 0)
                        chg   = round((lat - prev) / prev * 100, 2) if prev > 0 else None
                        price_map[ts] = {
                            "latest":   lat,
                            "pct_chg":  chg,
                            "low_252d": l252,
                            "low_756d": l756,
                            "low_all":  lall,
                            "rise_from_1y_low_pct":  round((lat - l252) / l252 * 100, 2) if l252 > 0 else None,
                            "rise_from_3y_low_pct":  round((lat - l756) / l756 * 100, 2) if l756 > 0 else None,
                            "rise_from_all_low_pct": round((lat - lall) / lall  * 100, 2) if lall  > 0 else None,
                        }
        except Exception as e:
            logger.warning(f"market.db price enrichment failed: {e}")

    # 3. 从 scoring.yaml 读取权重和分桶（一次，所有行复用）
    w_pb, w_pe, w_dist, dist_buckets = _load_scoring_params()

    # 4. 合并回 rows
    enriched = []
    for r in rows:
        row = dict(r)
        ts = row.get("ts_code", "")
        row["tdx_l1"] = l1_map.get(ts, "")
        p = price_map.get(ts, {})
        row["latest"]   = p.get("latest")
        row["pct_chg"]  = p.get("pct_chg")
        row["low_252d"] = p.get("low_252d")
        row["low_756d"] = p.get("low_756d")
        row["low_all"]  = p.get("low_all")
        row["rise_from_1y_low_pct"]  = p.get("rise_from_1y_low_pct")
        row["rise_from_3y_low_pct"]  = p.get("rise_from_3y_low_pct")
        row["rise_from_all_low_pct"] = p.get("rise_from_all_low_pct")

        # 3年维度评分：从 scoring.yaml 读取的分桶 + 权重，保持与主评分口径一致
        ds_3y = _apply_dist_buckets(p.get("rise_from_3y_low_pct"), dist_buckets)
        row["dist_score_3y"] = ds_3y
        pb_s  = row.get("pb_score")  or 0
        pe_s  = row.get("pe_score")  or 0
        if ds_3y is not None:
            row["score_3y"] = round(pb_s * w_pb + pe_s * w_pe + ds_3y * w_dist)
        else:
            row["score_3y"] = None

        enriched.append(row)
    return enriched


@router.post("/analysis/stock-pool/scan")
def scan_stock_pool():
    """Scan market data and populate stock pool using YAML-driven scoring"""
    try:
        import scoring

        config_path   = os.path.join(PROJECT_ROOT, "config", "scoring.yaml")
        base_path     = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
        enriched_path = os.path.join(PROJECT_ROOT, "market_enriched.csv")
        user_db_path  = os.path.join(PROJECT_ROOT, "user_data.db")

        if not os.path.isfile(base_path):
            raise HTTPException(status_code=400, detail="stock_industry_base.csv not found.")

        # 每次扫描都先重建 market_enriched.csv（确保用最新数据）
        # 失败时 _rebuild_enriched_csv 会抛 HTTPException，不会继续拿旧文件评分
        _rebuild_enriched_csv(base_path, enriched_path)

        result = scoring.run_scoring(
            config_path=config_path,
            base_path=base_path,
            enriched_path=enriched_path,
            user_db_path=user_db_path,
            min_score=70,
            write_csv=True,
        )

        return {
            "status": "success",
            "message": f"Scanned {result['total']} stocks. Found {result['pool_count']} candidates with Score >= 70.",
            "count": result["pool_count"]
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analysis/stock-pool")
def get_stock_pool():
    """Get stock pool with risk flags joined, enriched with market price data"""
    cache_key = _get_stock_pool_cache_key()
    cached = _STOCK_POOL_RESPONSE_CACHE
    if cached["key"] == cache_key and cached["value"] is not None:
        return deepcopy(cached["value"])

    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()

        try:
            c.execute("SELECT COUNT(*) as cnt FROM risk_flags")
            has_risk = c.fetchone()["cnt"] > 0
        except Exception:
            has_risk = False

        if has_risk:
            c.execute('''
                SELECT sp.*,
                       rf.ok_debt, rf.ok_goodwill, rf.ok_pledge,
                       rf.is_st, rf.has_major_event,
                       rf.risk_level, rf.negative_count,
                       rf.debt_ratio_pct, rf.goodwill_intangibles_pct,
                       rf.controller_pledge_pct
                FROM stock_pool sp
                LEFT JOIN risk_flags rf ON sp.ts_code = rf.ts_code
                ORDER BY sp.score DESC, sp.industry
            ''')
        else:
            c.execute("SELECT * FROM stock_pool ORDER BY score DESC, industry")

        rows = c.fetchall()

    enriched = _enrich_pool_rows(rows)
    response = {"status": "success", "data": enriched, "has_risk_data": has_risk}
    cached["key"] = cache_key
    cached["value"] = deepcopy(response)
    return response


@router.get("/analysis/scoring-config")
def get_scoring_config():
    """
    返回 scoring.yaml 中的权重和距低点分桶配置，供前端实时重算评分使用。
    保证前后端使用相同配置，YAML 调整后无需手动同步前端代码。
    """
    w_pb, w_pe, w_dist, buckets = _load_scoring_params()
    return {
        "status": "success",
        "weights": {
            "pb":                   w_pb,
            "pe_ttm":               w_pe,
            "distance_from_1y_low": w_dist,
        },
        "distance_buckets": buckets,
    }
