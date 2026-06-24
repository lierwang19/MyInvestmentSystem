# -*- coding: utf-8 -*-
"""
AI Holdings Briefing — batch-generates AI summaries for all held stocks.
Results are stored in ai_briefings table (one row per stock per day, overwrite on re-run).
"""
import json
import logging
import threading
import urllib.request
import urllib.error

from fastapi import APIRouter, HTTPException

from web.backend.database import get_db
from web.backend.config import USER_DB_PATH
from web.backend.api.ai_analysis import (
    _fetch_db_data, _fetch_news, _check_ollama,
    _fmt, to_plain_code,
    OLLAMA_BASE, OLLAMA_MODEL,
)
from web.backend.time_utils import sh_batch_id, sh_date_str, sh_iso_str, sh_now

logger = logging.getLogger(__name__)
router = APIRouter()

# ── In-memory progress tracker ────────────────────────────────────────────────

BRIEFING_PROGRESS: dict = {
    "status": "idle",      # idle / running / done / error
    "batch_id": None,
    "run_date": None,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "current_stock": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_progress_lock = threading.Lock()


# ── Ollama (non-streaming) ────────────────────────────────────────────────────

def _call_ollama_sync(prompt: str, timeout: int = 300) -> str:
    """Call Ollama and return the complete response text. Raises on failure."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": -1, "num_ctx": 8192},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    return result.get("response", "")


# ── Briefing prompt (shorter than single-stock analysis) ─────────────────────

def _build_briefing_prompt(ts_code: str, name: str, data: dict, news_titles: list[str]) -> str:
    risk = data.get("risk", {})
    pool = data.get("pool", {})
    prices = data.get("prices", [])
    val = data.get("valuation", {})

    industry = risk.get("industry") or pool.get("industry") or "未知"
    close = _fmt(prices[0]["close"] if prices else None)
    pb = _fmt(val.get("pb") or pool.get("pb"))
    pe = _fmt(val.get("pe_ttm") or pool.get("pe"))
    total_score = _fmt(pool.get("score"), ".0f")
    risk_level = {"low": "低", "medium": "中", "high": "高"}.get(
        risk.get("risk_level", ""), risk.get("risk_level", "未知")
    )

    news_summary = "、".join(news_titles[:3]) if news_titles else "暂无"

    return f"""你是一位专业的A股投资分析师。请用简洁的语言点评【{name}({ts_code})】：

数据：行业 {industry} | 收盘价 {close} 元 | PB {pb} | PE_TTM {pe}
打分：综合 {total_score}/100 | 风险等级 {risk_level}
近期新闻：{news_summary}

请输出（中文，不要 HTML 标签）：
1. 估值判断：一句话说明当前估值水平（低估/合理/高估）
2. 主要风险或亮点：最值得关注的一点
3. 操作建议：持有 / 加仓 / 减仓 / 观望"""


# ── Core batch function ───────────────────────────────────────────────────────

def run_holdings_briefing() -> dict:
    """
    Scan all distinct holdings, call Ollama for each, store in ai_briefings.
    Returns a summary dict. Safe to call from scheduler or manual trigger.
    """
    global BRIEFING_PROGRESS

    with _progress_lock:
        if BRIEFING_PROGRESS["status"] == "running":
            return {"error": "already_running", "batch_id": BRIEFING_PROGRESS["batch_id"]}

        BRIEFING_PROGRESS.update({
            "status": "running",
            "batch_id": None,
            "run_date": None,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "current_stock": None,
            "started_at": sh_iso_str(),
            "finished_at": None,
            "error": None,
        })

    try:
        # 1. Query distinct holdings (quantity > 0, deduplicated across accounts)
        with get_db() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ts_code, name FROM holdings WHERE quantity > 0"
            ).fetchall()

        stocks = [(r["ts_code"], r["name"] or r["ts_code"]) for r in rows]

        if not stocks:
            with _progress_lock:
                BRIEFING_PROGRESS.update({
                    "status": "done",
                    "finished_at": sh_iso_str(),
                    "error": "no_holdings",
                })
            return {"status": "done", "detail": "当前无持仓"}

        # 2. Generate batch_id and run_date
        now = sh_now()
        run_date = sh_date_str()
        batch_id = sh_batch_id()

        with _progress_lock:
            BRIEFING_PROGRESS.update({
                "batch_id": batch_id,
                "run_date": run_date,
                "total": len(stocks),
            })

        # 3. Insert/replace all pending rows — one commit, then close connection
        with get_db() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO ai_briefings
                   (batch_id, run_date, ts_code, name, status, content, error_msg, duration_sec)
                   VALUES (?, ?, ?, ?, 'pending', NULL, NULL, NULL)""",
                [(batch_id, run_date, ts_code, name) for ts_code, name in stocks]
            )
            conn.commit()

        # 4. Check Ollama availability — after pending rows are written so failures are visible
        if not _check_ollama():
            err_msg = f"Ollama 服务不可达（{OLLAMA_BASE}），请确认 Ollama 已启动"
            with get_db() as conn:
                conn.execute(
                    "UPDATE ai_briefings SET status='error', error_msg=? WHERE run_date=? AND batch_id=?",
                    (err_msg, run_date, batch_id)
                )
                conn.commit()
            with _progress_lock:
                BRIEFING_PROGRESS.update({
                    "status": "error",
                    "finished_at": sh_iso_str(),
                    "error": err_msg,
                })
            return {"status": "error", "detail": err_msg}

        # 5. Process each stock individually
        for ts_code, name in stocks:
            with _progress_lock:
                BRIEFING_PROGRESS["current_stock"] = f"{name}({ts_code})"

            t_start = sh_now()
            try:
                data = _fetch_db_data(ts_code)
                plain_code = to_plain_code(ts_code)
                news_titles = _fetch_news(plain_code, limit=3)
                prompt = _build_briefing_prompt(ts_code, name, data, news_titles)
                content = _call_ollama_sync(prompt, timeout=300)
                duration = (sh_now() - t_start).total_seconds()

                with get_db() as conn:
                    conn.execute(
                        """UPDATE ai_briefings
                           SET status='ok', content=?, duration_sec=?, batch_id=?, error_msg=NULL
                           WHERE run_date=? AND ts_code=?""",
                        (content, duration, batch_id, run_date, ts_code)
                    )
                    conn.commit()

                with _progress_lock:
                    BRIEFING_PROGRESS["completed"] += 1

                logger.info(f"AI Briefing OK: {name}({ts_code}) in {duration:.1f}s")

            except Exception as e:
                duration = (sh_now() - t_start).total_seconds()
                err_msg = str(e)[:500]
                logger.warning(f"AI Briefing FAIL: {name}({ts_code}) — {err_msg}")

                with get_db() as conn:
                    conn.execute(
                        """UPDATE ai_briefings
                           SET status='error', error_msg=?, duration_sec=?, batch_id=?
                           WHERE run_date=? AND ts_code=?""",
                        (err_msg, duration, batch_id, run_date, ts_code)
                    )
                    conn.commit()

                with _progress_lock:
                    BRIEFING_PROGRESS["failed"] += 1

        # 6. Final status
        with _progress_lock:
            prog = BRIEFING_PROGRESS
            BRIEFING_PROGRESS.update({
                "status": "done",
                "current_stock": None,
                "finished_at": sh_iso_str(),
            })

        return {
            "status": "done",
            "batch_id": batch_id,
            "run_date": run_date,
            "total": prog["total"],
            "completed": prog["completed"],
            "failed": prog["failed"],
        }

    except Exception as e:
        logger.exception("AI Briefing batch failed")
        with _progress_lock:
            BRIEFING_PROGRESS.update({
                "status": "error",
                "finished_at": sh_iso_str(),
                "error": str(e),
            })
        return {"status": "error", "detail": str(e)}


# ── Background runner ─────────────────────────────────────────────────────────

def _run_in_background():
    """Run briefing in a daemon thread (for manual trigger)."""
    t = threading.Thread(target=run_holdings_briefing, daemon=True)
    t.start()


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.post("/ai/briefings/run")
def trigger_briefing():
    """Manually trigger a briefing run. Returns immediately with batch_id (or 409 if running)."""
    with _progress_lock:
        if BRIEFING_PROGRESS["status"] == "running":
            raise HTTPException(
                status_code=409,
                detail=f"简报生成中，请稍候（当前：{BRIEFING_PROGRESS['current_stock']}）"
            )
    _run_in_background()
    return {"status": "started", "message": "简报生成已在后台启动"}


@router.get("/ai/briefings/progress")
def get_progress():
    """Return current briefing run progress."""
    with _progress_lock:
        return dict(BRIEFING_PROGRESS)


@router.get("/ai/briefings/dates")
def get_briefing_dates():
    """Return list of dates that have briefing records."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT run_date FROM ai_briefings ORDER BY run_date DESC LIMIT 90"
        ).fetchall()
    return {"dates": [r["run_date"] for r in rows]}


@router.get("/ai/briefings")
def get_briefings(date: str = None):
    """
    Return briefings for a given date (default: latest available date).
    Since UNIQUE(run_date, ts_code), each date has at most one briefing per stock.
    """
    with get_db() as conn:
        if not date:
            row = conn.execute(
                "SELECT run_date FROM ai_briefings ORDER BY run_date DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {"date": None, "briefings": [], "stats": {}}
            date = row["run_date"]

        rows = conn.execute(
            """SELECT ts_code, name, content, status, error_msg, duration_sec, batch_id, created_at
               FROM ai_briefings WHERE run_date=? ORDER BY status DESC, ts_code""",
            (date,)
        ).fetchall()

    briefings = [dict(r) for r in rows]
    total = len(briefings)
    ok = sum(1 for b in briefings if b["status"] == "ok")
    failed = sum(1 for b in briefings if b["status"] == "error")
    avg_dur = (
        sum(b["duration_sec"] or 0 for b in briefings if b["status"] == "ok") / ok
        if ok else 0
    )

    return {
        "date": date,
        "briefings": briefings,
        "stats": {
            "total": total,
            "ok": ok,
            "failed": failed,
            "avg_duration_sec": round(avg_dur, 1),
        },
    }
