# -*- coding: utf-8 -*-
"""
Risk Flags API — manages risk assessment/scanning for stocks.
Extracted from analysis.py for cleaner module separation.
"""
from fastapi import APIRouter, HTTPException
import os
import threading
import logging

from web.backend.config import PROJECT_ROOT
from web.backend.database import get_db
from web.backend.utils import dict_factory

logger = logging.getLogger(__name__)
router = APIRouter()

# ── 后台扫描状态（模块级，线程安全）──
_SCAN_STATE: dict = {
    "status":   "idle",   # idle | running | done | warning | error
    "progress": 0,
    "total":    0,
    "message":  "",
    "result":   None,
}
_SCAN_LOCK = threading.Lock()


def _get_tushare_token() -> str:
    """
    与 market.py 保持一致：优先从系统设置读取 token，再回退环境变量。
    """
    try:
        from web.backend.api.settings import get_setting_value
        token = get_setting_value("tushare_token", "")
        if token:
            return token
    except Exception:
        pass
    return os.environ.get("TUSHARE_TOKEN", "")


def _build_result_message(source: str, result: dict) -> tuple[str, str]:
    """返回 (status_out, message)。"""
    fetch_stats   = result.get("fetch_stats", {})
    data_warnings = []
    if source == "tushare" and result.get("total", 0) > 0:
        for col, label in [
            ("debt_ratio_pct",           "负债率"),
            ("goodwill_intangibles_pct", "商誉占比"),
            ("controller_pledge_pct",    "质押率"),
        ]:
            if fetch_stats.get(col, 0) == 0:
                data_warnings.append(f"{label}全部为空（可能 Token 权限不足）")

    status_out = "warning" if data_warnings else "done"
    msg = (
        f"扫描完成: {result['total']} 只。"
        f"高风险 {result['high']} / 中 {result['medium']} / 低 {result['low']}"
    )
    if data_warnings:
        msg += "。数据警告: " + "；".join(data_warnings)
    return status_out, msg


def _run_scan_background(base_path, user_db_path, skip_online, source, tushare_token, pool_only):
    """后台线程：执行 ETL，实时更新 _SCAN_STATE。"""
    import etl_financials

    def on_progress(current: int, total: int):
        with _SCAN_LOCK:
            _SCAN_STATE["progress"] = current
            _SCAN_STATE["total"]    = total

    try:
        result = etl_financials.run_etl(
            base_path=base_path,
            user_db_path=user_db_path,
            skip_online=skip_online,
            source=source,
            tushare_token=tushare_token,
            pool_only=pool_only,
            on_progress=on_progress,
        )
        status_out, msg = _build_result_message(source, result)
        with _SCAN_LOCK:
            _SCAN_STATE.update({
                "status":   status_out,
                "message":  msg,
                "result":   result,
                "progress": result.get("total", 0),
                "total":    result.get("total", 0),
            })
    except Exception as e:
        logger.exception("后台财务扫描失败")
        with _SCAN_LOCK:
            _SCAN_STATE.update({"status": "error", "message": str(e), "result": None})


# ── API 端点 ──────────────────────────────────────────────

@router.get("/analysis/risk-flags")
def get_risk_flags(industry: str = None, risk_level: str = None):
    """Get risk flags data, optionally filtered"""
    with get_db() as conn:
        conn.row_factory = dict_factory
        c = conn.cursor()

        try:
            c.execute("SELECT COUNT(*) as cnt FROM risk_flags")
            count = c.fetchone()["cnt"]
        except Exception:
            return {"status": "warning", "message": "risk_flags 表不存在或为空，请先运行风险标记扫描", "data": []}

        if count == 0:
            return {"status": "warning", "message": "无风险标记数据，请先运行风险标记扫描", "data": []}

        query = "SELECT * FROM risk_flags WHERE 1=1"
        params = []
        if industry:
            query += " AND industry = ?"
            params.append(industry)
        if risk_level:
            query += " AND risk_level = ?"
            params.append(risk_level)
        query += " ORDER BY negative_count DESC, ts_code"

        c.execute(query, params)
        rows = c.fetchall()
        return {"status": "success", "data": rows, "total": len(rows)}


@router.get("/analysis/risk-flags/scan-status")
def get_scan_status():
    """返回当前财务扫描进度（前端轮询用）。"""
    with _SCAN_LOCK:
        return dict(_SCAN_STATE)


@router.post("/analysis/risk-flags/scan")
def scan_risk_flags(
    skip_online: bool = True,
    source: str = None,       # "offline" | "akshare" | "tushare"
    pool_only: bool = False,  # True = 只扫优选池标的
):
    """
    触发风险标记 ETL 扫描。

    - source=offline（默认）：同步执行，仅 ST 检测，秒级返回
    - source=tushare&pool_only=true：后台异步执行，立即返回 started，
      前端通过 GET /scan-status 轮询进度
    - Tushare Token 读取优先级：系统设置 > TUSHARE_TOKEN 环境变量
    """
    # 推断实际 source
    effective_source = source or ("offline" if skip_online else "akshare")

    base_path    = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    user_db_path = os.path.join(PROJECT_ROOT, "user_data.db")

    if not os.path.exists(base_path):
        raise HTTPException(status_code=400, detail="stock_industry_base.csv not found.")

    # ── Tushare 模式：后台异步执行 ──
    if effective_source == "tushare":
        with _SCAN_LOCK:
            if _SCAN_STATE["status"] == "running":
                prog = _SCAN_STATE["progress"]
                total = _SCAN_STATE["total"]
                return {
                    "status":  "running",
                    "message": f"扫描正在进行中 ({prog}/{total})，请稍候...",
                    "progress": prog,
                    "total":    total,
                }
            _SCAN_STATE.update({
                "status":   "running",
                "progress": 0,
                "total":    0,
                "message":  "正在初始化...",
                "result":   None,
            })

        tushare_token = _get_tushare_token()
        if not tushare_token:
            with _SCAN_LOCK:
                _SCAN_STATE["status"] = "idle"
            raise HTTPException(status_code=400, detail="未配置 Tushare Token，请在系统设置中填写")

        t = threading.Thread(
            target=_run_scan_background,
            args=(base_path, user_db_path, skip_online, effective_source, tushare_token, pool_only),
            daemon=True,
        )
        t.start()
        return {"status": "started", "message": "财务扫描已启动，请通过进度接口查询结果"}

    # ── offline / akshare 模式：同步执行 ──
    try:
        import etl_financials

        result = etl_financials.run_etl(
            base_path=base_path,
            user_db_path=user_db_path,
            skip_online=skip_online,
            source=effective_source,
            tushare_token="",
            pool_only=pool_only,
        )
        _, msg = _build_result_message(effective_source, result)
        return {"status": "success", "message": msg, **result}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
