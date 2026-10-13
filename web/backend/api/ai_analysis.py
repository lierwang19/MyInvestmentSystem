# -*- coding: utf-8 -*-
"""
AI Risk Analysis API — streams LLM analysis for individual stocks.
Data sources: SQLite DBs + akshare news + workstation sglang (qwen).
"""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from web.backend.config import USER_DB_PATH, MARKET_DB_PATH
from web.backend.database import get_db, get_market_db
from web.backend.services import sglang_client

logger = logging.getLogger(__name__)
router = APIRouter()


# ── helpers ───────────────────────────────────────────────────────────────────

def to_plain_code(ts_code: str) -> str:
    """601318.SH → 601318  (for akshare and other non-ts interfaces)"""
    return ts_code.split(".")[0]


def _fmt(val, fmt=".2f", fallback="—"):
    try:
        return format(float(val), fmt) if val is not None else fallback
    except (TypeError, ValueError):
        return fallback


# ── data fetching ─────────────────────────────────────────────────────────────

def _fetch_db_data(ts_code: str) -> dict:
    """
    Pull all relevant data for ts_code from user_data.db and market.db.
    Raises RuntimeError if critical tables are inaccessible.
    """
    data: dict = {}

    # user_data.db
    try:
        with get_db() as conn:
            c = conn.cursor()

            c.execute("SELECT * FROM risk_flags WHERE ts_code=? LIMIT 1", (ts_code,))
            row = c.fetchone()
            data["risk"] = dict(row) if row else {}

            c.execute("SELECT * FROM stock_pool WHERE ts_code=? LIMIT 1", (ts_code,))
            row = c.fetchone()
            data["pool"] = dict(row) if row else {}
    except Exception as e:
        raise RuntimeError(f"user_data.db 查询失败: {e}")

    # market.db
    try:
        with get_market_db() as conn:
            if not conn:
                raise RuntimeError("market.db 无法连接")
            c = conn.cursor()

            c.execute(
                "SELECT trade_date, close FROM daily_price WHERE ts_code=? ORDER BY trade_date DESC LIMIT 5",
                (ts_code,),
            )
            data["prices"] = [dict(r) for r in c.fetchall()]

            c.execute(
                "SELECT pb, pe_ttm FROM daily_valuation WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
                (ts_code,),
            )
            row = c.fetchone()
            data["valuation"] = dict(row) if row else {}

            c.execute(
                "SELECT bvps, eps_ttm FROM fin_indicators_q WHERE ts_code=? ORDER BY period_end DESC LIMIT 1",
                (ts_code,),
            )
            row = c.fetchone()
            data["fin"] = dict(row) if row else {}
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"market.db 查询失败: {e}")

    return data


def _fetch_news(plain_code: str, limit: int = 8) -> list[str]:
    """Fetch recent news titles via akshare. Returns [] on failure."""
    try:
        import akshare as ak  # lazy import
        df = ak.stock_news_em(symbol=plain_code)
        if df is None or df.empty:
            return []
        return df["新闻标题"].head(limit).tolist()
    except Exception as e:
        logger.warning(f"akshare 新闻获取失败 ({plain_code}): {e}")
        return []


# ── prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(ts_code: str, name: str, data: dict, news_titles: list[str]) -> str:
    risk = data.get("risk", {})
    pool = data.get("pool", {})
    prices = data.get("prices", [])
    val = data.get("valuation", {})
    fin = data.get("fin", {})

    industry = risk.get("industry") or pool.get("industry") or "未知"
    close = _fmt(prices[0]["close"] if prices else None)
    price_trend = " → ".join(_fmt(p["close"]) for p in reversed(prices)) if prices else "—"

    pb = _fmt(val.get("pb") or pool.get("pb"))
    pe = _fmt(val.get("pe_ttm") or pool.get("pe"))
    bvps = _fmt(fin.get("bvps"))
    eps = _fmt(fin.get("eps_ttm"))

    # stock_pool 真实字段：score / pb_score / pe_score / dist_score
    total_score = _fmt(pool.get("score"), ".0f")
    pb_score    = _fmt(pool.get("pb_score"), ".0f")
    pe_score    = _fmt(pool.get("pe_score"), ".0f")
    dist_score  = _fmt(pool.get("dist_score"), ".0f")

    debt = _fmt(risk.get("debt_ratio_pct"), ".1f")
    goodwill = _fmt(risk.get("goodwill_intangibles_pct"), ".1f")
    pledge = _fmt(risk.get("controller_pledge_pct"), ".2f")
    risk_level = {"low": "低", "medium": "中", "high": "高"}.get(
        risk.get("risk_level", ""), risk.get("risk_level", "未知")
    )

    if news_titles:
        news_section = "\n".join(f"{i+1}. {t}" for i, t in enumerate(news_titles))
    else:
        news_section = "暂无新闻数据"

    return f"""你是一位专业的A股投资分析师。请根据以下数据对【{name}({ts_code})】进行风险分析。

## 基本面数据
- 行业: {industry}
- 最新收盘价: {close} 元
- 近5日价格走势: {price_trend}

## 估值指标
- PB(市净率): {pb}
- PE_TTM(滚动市盈率): {pe}
- 每股净资产(BVPS): {bvps} 元
- 每股收益(EPS_TTM): {eps} 元

## 打分系统结果
- 综合得分: {total_score}/100
- PB分: {pb_score} | PE分: {pe_score} | 距低点分: {dist_score}

## 风险标志
- 资产负债率: {debt}%
- (商誉+无形资产)/总资产: {goodwill}%
- 控股股东质押比例: {pledge}%
- 系统判定风险等级: {risk_level}

## 近期新闻（最近8条标题）
{news_section}

---

请输出以下分析（中文，简洁，不要使用 HTML 标签）：
1. 综合风险评级: 低风险/中风险/高风险，一句话理由
2. 估值判断: 低估/合理/高估，参考PB和PE水平
3. 主要风险点: 2-3个
4. 新闻情绪: 正面/中性/偏负面，简要说明
5. 操作建议: 一句话总结"""


# ── streaming generator ───────────────────────────────────────────────────────

def _sse_data(text: str) -> str:
    """Encode text as one SSE event; embedded newlines become multiple data lines."""
    return "".join(f"data: {part}\n" for part in text.split("\n")) + "\n"


def _stream_llm(prompt: str):
    """Generator: yield SSE events from sglang streaming response."""
    has_output = False
    try:
        for token in sglang_client.chat_stream(prompt, timeout=180):
            has_output = True
            yield _sse_data(token)
        yield "event: done\ndata: \n\n"
    except Exception as e:
        logger.exception("sglang 流式调用失败")
        # 保留已输出的部分内容，错误提示另起一段
        prefix = "\n\n" if has_output else ""
        yield "event: error\n" + _sse_data(f"{prefix}LLM调用异常: {e}")


# ── endpoint ──────────────────────────────────────────────────────────────────

@router.get("/ai/analyze")
def analyze_stock(ts_code: str, name: str = ""):
    """
    Stream AI risk analysis for a stock.
    Phase 1 (sync): validate data + fetch news
    Phase 2 (stream): call sglang and SSE-stream tokens
    """
    if not ts_code:
        raise HTTPException(status_code=400, detail="ts_code 不能为空")

    # Phase 1a: DB data
    try:
        data = _fetch_db_data(ts_code)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Phase 1b: news (failure is non-fatal)
    plain_code = to_plain_code(ts_code)
    news_titles = _fetch_news(plain_code)

    # Phase 1c: check sglang
    if not sglang_client.check_sglang():
        raise HTTPException(
            status_code=503,
            detail=f"AI 服务未就绪：{sglang_client.unreachable_msg()}",
        )

    # Phase 2: build prompt + stream
    display_name = name or data.get("risk", {}).get("name") or data.get("pool", {}).get("name") or ts_code
    prompt = _build_prompt(ts_code, display_name, data, news_titles)

    return StreamingResponse(
        _stream_llm(prompt),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering via header too
        },
    )
