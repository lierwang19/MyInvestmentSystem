# -*- coding: utf-8 -*-
"""
engine.py — 策略引擎核心
========================
三层架构:
  1. generate_signals()  — 信号生成 (候选资格)
  2. make_decisions()    — 持仓决策 (最终操作建议)
  3. run_strategy()      — 编排入口 (调用上面两层 + 写库)

当前实现: 大数投资 (dashu_v1) 策略
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime

from web.backend.time_utils import sh_date_str, sh_timestamp_str
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


# ===================== 数据结构 =====================

@dataclass
class Signal:
    ts_code: str
    name: str
    industry: str
    signal: str               # BUY_CANDIDATE / SELL_CANDIDATE / HOLD / SKIP
    score: Optional[float] = None
    pb: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb_anchor: Optional[float] = None
    pb_ratio: Optional[float] = None
    rise_from_1y_low_pct: Optional[float] = None
    reason_code: str = ""
    reason_text: str = ""
    sell_ratio: float = 0.0   # matched sell ratio; non-zero only for SELL_CANDIDATE
    explain: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    ts_code: str
    name: str
    industry: str
    action: str               # BUY / SELL / NO_ACTION
    quantity: int = 0
    reason_code: str = ""     # first_buy / add_position / sell_half / blocked_by_cash / ...
    reason_text: str = ""
    blocked_by: Optional[str] = None  # None / cash / industry_limit / position_limit
    current_price: float = 0.0
    target_amount: float = 0.0
    industry_position_pct: float = 0.0
    total_position_pct: float = 0.0
    explain: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0


@dataclass
class StrategyRunResult:
    strategy_id: str
    run_date: str
    run_id: int = 0
    signals: List[Signal] = field(default_factory=list)
    decisions: List[Decision] = field(default_factory=list)
    error: Optional[str] = None


# ===================== 配置加载 =====================

def load_strategy_config(config_path: str) -> Dict[str, Any]:
    """Load strategy YAML configuration."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _to_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (ValueError, TypeError):
        return None


def _fetch_market_prices(market_db_path: str, ts_codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    从 market.db 查每只股票的最新收盘价和近一年最低价。
    返回: {ts_code: {close_price, min_1y_low, rise_from_1y_low_pct}}
    """
    if not os.path.exists(market_db_path) or not ts_codes:
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(market_db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        placeholders = ",".join("?" * len(ts_codes))

        # 最新收盘价
        c.execute(f"""
            SELECT dp.ts_code, dp.close
            FROM daily_price dp
            INNER JOIN (
                SELECT ts_code, MAX(trade_date) AS max_date
                FROM daily_price
                WHERE ts_code IN ({placeholders})
                GROUP BY ts_code
            ) latest ON dp.ts_code = latest.ts_code AND dp.trade_date = latest.max_date
        """, ts_codes)
        for row in c.fetchall():
            v = _to_float(row["close"])
            if v and v > 0:
                result[row["ts_code"]] = {"close_price": v}

        # 近一年最低价（用 low 字段）
        c.execute(f"""
            SELECT ts_code, MIN(low) AS min_1y_low
            FROM daily_price
            WHERE ts_code IN ({placeholders})
              AND trade_date >= date('now', '-365 days')
            GROUP BY ts_code
        """, ts_codes)
        for row in c.fetchall():
            ts = row["ts_code"]
            min_low = _to_float(row["min_1y_low"])
            if ts in result and min_low and min_low > 0:
                close = result[ts]["close_price"]
                rise_pct = round((close - min_low) / min_low * 100, 2)
                result[ts]["min_1y_low"] = min_low
                result[ts]["rise_from_1y_low_pct"] = rise_pct

        conn.close()
    except Exception as e:
        logger.warning(f"_fetch_market_prices 失败: {e}")

    return result


# ===================== 第一层: 信号生成 =====================

def generate_signals(
    strategy_config: Dict[str, Any],
    market_data: List[Dict[str, Any]],
    holdings_map: Dict[str, Dict[str, Any]],
    market_pb: Optional[float] = None,
    run_date: str = "",
) -> List[Signal]:
    """
    Generate signals for each stock based on strategy rules.

    Args:
        strategy_config: Parsed dashu_v1.yaml config
        market_data: List of dicts from stock_pool or market_enriched
                     Each dict must have: ts_code, name, industry, pb, pe/pe_ttm,
                                          anchor_pb, score, rise_from_1y_low_pct
        holdings_map: {ts_code: {quantity, cost_price, ...}} current holdings
        market_pb: Current market-wide PB (if available)
        run_date: Date string for the run

    Returns:
        List of Signal objects
    """
    buy_rules = strategy_config.get("buy_rules", {})
    sell_rules = strategy_config.get("sell_rules", {})
    entry = buy_rules.get("entry", {})

    max_pb = float(entry.get("max_pb", 2.0))
    max_pe = float(entry.get("max_pe", 20.0))
    market_pb_halt = float(entry.get("market_pb_halt", 2.5))

    sell_method = sell_rules.get("method", "from_1y_low")
    sell_thresholds = sell_rules.get(sell_method, {}).get("thresholds", [])

    # Check market-wide halt
    market_halted = False
    if market_pb is not None and market_pb > market_pb_halt:
        market_halted = True

    signals: List[Signal] = []

    for row in market_data:
        ts_code = str(row.get("ts_code", ""))
        name = str(row.get("name", ""))
        industry = str(row.get("industry", ""))
        pb = _to_float(row.get("pb"))
        pe_ttm = _to_float(row.get("pe_ttm") or row.get("pe"))
        anchor_pb = _to_float(row.get("anchor_pb") or row.get("pb_anchor"))
        score = _to_float(row.get("score"))
        rise_pct = _to_float(row.get("rise_from_1y_low_pct"))
        close_price = _to_float(row.get("close_price"))

        pb_ratio = None
        if pb is not None and anchor_pb is not None and anchor_pb > 0:
            pb_ratio = round(pb / anchor_pb, 3)

        explain = {
            "pb": pb,
            "pe_ttm": pe_ttm,
            "pb_anchor": anchor_pb,
            "pb_ratio": pb_ratio,
            "score": score,
            "rise_from_1y_low_pct": rise_pct,
            "close_price": close_price,
            "market_pb": market_pb,
        }

        is_held = ts_code in holdings_map

        # ── 持仓路径（优先处理，避免被误判为新买入候选）──
        if is_held:
            if rise_pct is not None:
                # 检查是否达到卖出阈值
                matched_threshold = None
                if sell_method == "from_buy_pb":
                    # 优先用持久化的买入时 PB；历史持仓 buy_pb=0 时回退到当前 PB
                    stored_buy_pb = float((holdings_map.get(ts_code) or {}).get("buy_pb") or 0.0)
                    if stored_buy_pb > 0:
                        holding_pb = stored_buy_pb
                    else:
                        holding_pb = pb  # 回退：历史持仓未记录 buy_pb
                        logger.warning(
                            f"{ts_code}: buy_pb 未记录，用当前 PB={pb:.2f} 近似分桶（建议补录首买 PB）"
                        )
                    for th in sorted(sell_thresholds, key=lambda t: float(t.get("buy_pb_max", 99))):
                        if holding_pb is not None and holding_pb <= float(th.get("buy_pb_max", 99)):
                            if rise_pct >= float(th.get("rise_pct", 100)):
                                matched_threshold = th
                            break  # 找到匹配的 PB 桶即止
                else:
                    # from_1y_low: 取第一个满足涨幅的阈值
                    for th in sell_thresholds:
                        threshold_pct = float(th.get("rise_pct", 100))
                        if rise_pct >= threshold_pct:
                            matched_threshold = th
                            break

                if matched_threshold is not None:
                    threshold_pct = float(matched_threshold.get("rise_pct", 100))
                    sell_ratio = float(matched_threshold.get("sell_ratio", 0.5))
                    if sell_method == "from_buy_pb":
                        reason_text = (
                            f"PB桶≤{matched_threshold.get('buy_pb_max')},"
                            f"涨幅{rise_pct:.1f}%≥{threshold_pct}%,卖出{int(sell_ratio*100)}%"
                        )
                    else:
                        reason_text = f"离一年低点涨幅{rise_pct:.1f}%已达{threshold_pct}%阈值,卖出{int(sell_ratio*100)}%"
                    signals.append(Signal(
                        ts_code=ts_code, name=name, industry=industry,
                        signal="SELL_CANDIDATE",
                        score=score, pb=pb, pe_ttm=pe_ttm,
                        pb_anchor=anchor_pb, pb_ratio=pb_ratio,
                        rise_from_1y_low_pct=rise_pct,
                        reason_code="sell_on_rise",
                        reason_text=reason_text,
                        sell_ratio=sell_ratio,
                        explain={**explain,
                                 "sell_threshold_pct": threshold_pct,
                                 "sell_ratio": sell_ratio,
                                 "sell_method": sell_method},
                    ))
                else:
                    # 持有中，未达到卖出阈值
                    signals.append(Signal(
                        ts_code=ts_code, name=name, industry=industry,
                        signal="HOLD",
                        score=score, pb=pb, pe_ttm=pe_ttm,
                        pb_anchor=anchor_pb, pb_ratio=pb_ratio,
                        rise_from_1y_low_pct=rise_pct,
                        reason_code="holding",
                        reason_text="持有中，未达到卖出阈值",
                        explain=explain,
                    ))
            else:
                # rise_pct 缺失（新股/行情不足）：仍按持有处理，暂停卖出判断
                signals.append(Signal(
                    ts_code=ts_code, name=name, industry=industry,
                    signal="HOLD",
                    score=score, pb=pb, pe_ttm=pe_ttm,
                    pb_anchor=anchor_pb, pb_ratio=pb_ratio,
                    rise_from_1y_low_pct=None,
                    reason_code="holding_no_rise_data",
                    reason_text="持有中，暂无一年低点数据，跳过卖出判断",
                    explain=explain,
                ))
            continue

        # ── 买入信号判断 ──
        if market_halted:
            signals.append(Signal(
                ts_code=ts_code, name=name, industry=industry,
                signal="SKIP",
                score=score, pb=pb, pe_ttm=pe_ttm,
                pb_anchor=anchor_pb, pb_ratio=pb_ratio,
                rise_from_1y_low_pct=rise_pct,
                reason_code="market_pb_halt",
                reason_text=f"市场PB({market_pb:.2f})超过{market_pb_halt}，暂停买入",
                explain=explain,
            ))
            continue

        # Check PB/PE entry conditions
        pb_ok = pb is not None and pb < max_pb and pb > 0
        pe_ok = pe_ttm is not None and pe_ttm < max_pe and pe_ttm > 0

        if pb_ok and pe_ok:
            signals.append(Signal(
                ts_code=ts_code, name=name, industry=industry,
                signal="BUY_CANDIDATE",
                score=score, pb=pb, pe_ttm=pe_ttm,
                pb_anchor=anchor_pb, pb_ratio=pb_ratio,
                rise_from_1y_low_pct=rise_pct,
                reason_code="entry_qualified",
                reason_text=f"PB={pb:.2f}<{max_pb}, PE={pe_ttm:.1f}<{max_pe}",
                explain=explain,
            ))
        else:
            skip_reasons = []
            if not pb_ok:
                skip_reasons.append(f"PB={'N/A' if pb is None else f'{pb:.2f}'}≥{max_pb}")
            if not pe_ok:
                skip_reasons.append(f"PE={'N/A' if pe_ttm is None else f'{pe_ttm:.1f}'}≥{max_pe}")
            signals.append(Signal(
                ts_code=ts_code, name=name, industry=industry,
                signal="SKIP",
                score=score, pb=pb, pe_ttm=pe_ttm,
                pb_anchor=anchor_pb, pb_ratio=pb_ratio,
                rise_from_1y_low_pct=rise_pct,
                reason_code="entry_failed",
                reason_text="; ".join(skip_reasons),
                explain=explain,
            ))

    return signals


# ===================== 第二层: 持仓决策 =====================

def make_decisions(
    strategy_config: Dict[str, Any],
    signals: List[Signal],
    holdings: List[Dict[str, Any]],
    account: Dict[str, Any],
    run_date: str = "",
) -> List[Decision]:
    """
    Convert signals into actionable decisions, considering:
    - Current holdings and positions
    - Cash balance
    - Industry limits
    - Total position limits

    Args:
        strategy_config: Parsed dashu_v1.yaml config
        signals: Output from generate_signals()
        holdings: List of holding dicts {ts_code, name, quantity, cost_price, market_value, industry}
        account: {cash_balance, total_assets}
        run_date: Date string

    Returns:
        List of Decision objects (only BUY/SELL actions + blocked reasons)
    """
    buy_rules = strategy_config.get("buy_rules", {})
    sell_rules = strategy_config.get("sell_rules", {})
    limits = buy_rules.get("position_limits", {})
    batch_cfg = buy_rules.get("batch", {})
    entry_cfg = buy_rules.get("entry", {})

    max_industry_pct = float(limits.get("max_industry_pct", 3.0))
    max_total_pct = float(limits.get("max_total_position_pct", 66.7))
    max_pb = float(entry_cfg.get("max_pb", 2.0))
    max_pe = float(entry_cfg.get("max_pe", 20.0))

    # 分批建仓参数
    high_pb_threshold = float(batch_cfg.get("high_pb_threshold", 1.5))
    high_pb_drop_pct = float(batch_cfg.get("high_pb_drop_pct", 20.0))
    low_pb_drop_pct = float(batch_cfg.get("low_pb_drop_pct", 10.0))

    sell_method = sell_rules.get("method", "from_1y_low")
    sell_thresholds = sell_rules.get(sell_method, {}).get("thresholds", [])

    cash = float(account.get("cash_balance", 0))
    total_assets = float(account.get("total_assets", 0))
    if total_assets <= 0:
        total_assets = cash  # If no holdings, total_assets = cash

    # Build holdings lookup
    holdings_by_code = {}
    industry_mv = {}  # industry -> total market value
    total_mv = 0.0

    for h in holdings:
        ts = str(h.get("ts_code", ""))
        mv = float(h.get("market_value", 0))
        ind = str(h.get("industry", ""))
        holdings_by_code[ts] = h
        industry_mv[ind] = industry_mv.get(ind, 0) + mv
        total_mv += mv

    total_position_pct = (total_mv / total_assets * 100) if total_assets > 0 else 0

    decisions: List[Decision] = []
    priority_counter = 0

    for sig in signals:
        # ── 卖出决策 ──
        if sig.signal == "SELL_CANDIDATE":
            held = holdings_by_code.get(sig.ts_code)
            if not held:
                continue

            qty = int(held.get("quantity", 0))
            if qty <= 0:
                continue

            # Use sell_ratio already resolved during signal generation
            # (respects the matched buy_pb_max bucket for from_buy_pb, or
            #  the first qualifying rise_pct threshold for from_1y_low)
            sell_ratio = sig.sell_ratio if sig.sell_ratio > 0 else 0.5

            sell_qty = (int(qty * sell_ratio) // 100) * 100  # sell_ratio is 0~1; round down to lots of 100
            if sell_qty > qty:
                sell_qty = qty
            # Ensure it's a multiple of 100 (A-share lot size) unless selling all
            if sell_qty < qty:
                sell_qty = (sell_qty // 100) * 100
                if sell_qty == 0:
                    sell_qty = qty  # If less than 100 shares, sell all

            current_price = float(held.get("current_price", held.get("cost_price", 0)))
            ind = sig.industry
            ind_pct = (industry_mv.get(ind, 0) / total_assets * 100) if total_assets > 0 else 0

            priority_counter += 1
            decisions.append(Decision(
                ts_code=sig.ts_code,
                name=sig.name,
                industry=ind,
                action="SELL",
                quantity=sell_qty,
                reason_code="sell_on_rise",
                reason_text=sig.reason_text,
                current_price=current_price,
                target_amount=round(sell_qty * current_price, 2),
                industry_position_pct=round(ind_pct, 2),
                total_position_pct=round(total_position_pct, 2),
                explain={
                    **sig.explain,
                    "sell_ratio": sell_ratio,
                    "current_qty": qty,
                    "sell_qty": sell_qty,
                },
                priority=priority_counter,
            ))

        # ── 买入决策 ──
        elif sig.signal == "BUY_CANDIDATE":
            ind = sig.industry
            ind_mv = industry_mv.get(ind, 0)
            ind_pct = (ind_mv / total_assets * 100) if total_assets > 0 else 0

            # Check industry limit
            if ind_pct >= max_industry_pct:
                decisions.append(Decision(
                    ts_code=sig.ts_code, name=sig.name, industry=ind,
                    action="NO_ACTION",
                    reason_code="blocked_by_industry",
                    reason_text=f"行业{ind}仓位{ind_pct:.1f}%已达上限{max_industry_pct}%",
                    blocked_by="industry_limit",
                    industry_position_pct=round(ind_pct, 2),
                    total_position_pct=round(total_position_pct, 2),
                    explain=sig.explain,
                ))
                continue

            # Check total position limit
            if total_position_pct >= max_total_pct:
                decisions.append(Decision(
                    ts_code=sig.ts_code, name=sig.name, industry=ind,
                    action="NO_ACTION",
                    reason_code="blocked_by_position",
                    reason_text=f"总仓位{total_position_pct:.1f}%已达上限{max_total_pct}%",
                    blocked_by="position_limit",
                    industry_position_pct=round(ind_pct, 2),
                    total_position_pct=round(total_position_pct, 2),
                    explain=sig.explain,
                ))
                continue

            # ── 获取收盘价（必须有，否则无法计算股数）──
            current_price = _to_float(sig.explain.get("close_price"))
            if not current_price or current_price <= 0:
                decisions.append(Decision(
                    ts_code=sig.ts_code, name=sig.name, industry=ind,
                    action="NO_ACTION",
                    reason_code="no_price",
                    reason_text="无最新收盘价，无法计算买入股数",
                    industry_position_pct=round(ind_pct, 2),
                    total_position_pct=round(total_position_pct, 2),
                    explain=sig.explain,
                ))
                continue

            # ── 现金检查 ──
            available_for_industry = max(total_assets * max_industry_pct / 100 - ind_mv, 0)
            if cash <= 0:
                decisions.append(Decision(
                    ts_code=sig.ts_code, name=sig.name, industry=ind,
                    action="NO_ACTION",
                    reason_code="blocked_by_cash",
                    reason_text=f"现金不足(¥{cash:.0f})",
                    blocked_by="cash",
                    industry_position_pct=round(ind_pct, 2),
                    total_position_pct=round(total_position_pct, 2),
                    explain=sig.explain,
                ))
                continue

            # ── 计算买入金额和股数 ──
            buy_budget = min(available_for_industry, cash)
            buy_qty = int(buy_budget / current_price / 100) * 100
            if buy_qty <= 0:
                # 预算不足一手，最多尝试用现金买一手
                if cash >= current_price * 100:
                    buy_qty = 100
                else:
                    decisions.append(Decision(
                        ts_code=sig.ts_code, name=sig.name, industry=ind,
                        action="NO_ACTION",
                        reason_code="blocked_by_cash",
                        reason_text=f"现金不足一手(¥{cash:.0f} < ¥{current_price * 100:.0f})",
                        blocked_by="cash",
                        industry_position_pct=round(ind_pct, 2),
                        total_position_pct=round(total_position_pct, 2),
                        explain=sig.explain,
                    ))
                    continue

            buy_amount = round(buy_qty * current_price, 2)
            # 二次校验现金充足
            if buy_amount > cash:
                buy_qty = int(cash / current_price / 100) * 100
                if buy_qty <= 0:
                    continue
                buy_amount = round(buy_qty * current_price, 2)

            reason_code = "first_buy"
            reason_text = f"首次建仓: PB={sig.pb:.2f}, PE={sig.pe_ttm:.1f}" if sig.pb and sig.pe_ttm else "首次建仓"

            priority_counter += 1
            decisions.append(Decision(
                ts_code=sig.ts_code,
                name=sig.name,
                industry=ind,
                action="BUY",
                quantity=buy_qty,
                reason_code=reason_code,
                reason_text=reason_text,
                current_price=current_price,
                target_amount=buy_amount,
                industry_position_pct=round(ind_pct, 2),
                total_position_pct=round(total_position_pct, 2),
                explain={
                    **sig.explain,
                    "buy_budget": round(buy_budget, 2),
                    "buy_qty": buy_qty,
                },
                priority=priority_counter,
            ))
            # ── 实时扣减：让后续信号看到准确的现金/仓位约束 ──
            cash -= buy_amount
            industry_mv[ind] = industry_mv.get(ind, 0) + buy_amount
            # 重算 total_position_pct（total_assets 不变，因为现金→持仓只是转换）
            total_mv = sum(industry_mv.values())
            total_position_pct = (total_mv / total_assets * 100) if total_assets > 0 else 0

        # ── HOLD：检查是否满足分批加仓条件 ──
        elif sig.signal == "HOLD":
            held = holdings_by_code.get(sig.ts_code)
            if not held:
                continue

            # 入场估值仍需满足 PB/PE 条件
            if sig.pb is None or sig.pb >= max_pb:
                continue
            if sig.pe_ttm is None or sig.pe_ttm >= max_pe:
                continue

            current_price = _to_float(sig.explain.get("close_price"))
            if not current_price or current_price <= 0:
                continue

            cost_price = float(held.get("cost_price", 0))
            if cost_price <= 0:
                continue

            # 根据当前 PB 判断需要下跌的幅度（偏高入场：20%，偏低入场：10%）
            required_drop_pct = high_pb_drop_pct if (sig.pb > high_pb_threshold) else low_pb_drop_pct
            actual_drop_pct = (cost_price - current_price) / cost_price * 100

            if actual_drop_pct < required_drop_pct:
                # 跌幅不够，不加仓（不写入 decisions，静默跳过）
                continue

            # 通过加仓检查，再走仓位限制 → 现金 → 股数计算
            ind = sig.industry
            ind_mv = industry_mv.get(ind, 0)
            ind_pct = (ind_mv / total_assets * 100) if total_assets > 0 else 0

            if ind_pct >= max_industry_pct:
                decisions.append(Decision(
                    ts_code=sig.ts_code, name=sig.name, industry=ind,
                    action="NO_ACTION",
                    reason_code="blocked_by_industry",
                    reason_text=f"加仓被行业仓位限制: {ind} {ind_pct:.1f}%≥{max_industry_pct}%",
                    blocked_by="industry_limit",
                    industry_position_pct=round(ind_pct, 2),
                    total_position_pct=round(total_position_pct, 2),
                    explain=sig.explain,
                ))
                continue

            if total_position_pct >= max_total_pct:
                decisions.append(Decision(
                    ts_code=sig.ts_code, name=sig.name, industry=ind,
                    action="NO_ACTION",
                    reason_code="blocked_by_position",
                    reason_text=f"加仓被总仓位限制: {total_position_pct:.1f}%≥{max_total_pct}%",
                    blocked_by="position_limit",
                    industry_position_pct=round(ind_pct, 2),
                    total_position_pct=round(total_position_pct, 2),
                    explain=sig.explain,
                ))
                continue

            if cash <= 0:
                decisions.append(Decision(
                    ts_code=sig.ts_code, name=sig.name, industry=ind,
                    action="NO_ACTION",
                    reason_code="blocked_by_cash",
                    reason_text=f"现金不足(¥{cash:.0f})",
                    blocked_by="cash",
                    industry_position_pct=round(ind_pct, 2),
                    total_position_pct=round(total_position_pct, 2),
                    explain=sig.explain,
                ))
                continue

            available_for_industry = max(total_assets * max_industry_pct / 100 - ind_mv, 0)
            buy_budget = min(available_for_industry, cash)
            buy_qty = int(buy_budget / current_price / 100) * 100
            if buy_qty <= 0 and cash >= current_price * 100:
                buy_qty = 100
            if buy_qty <= 0:
                continue

            buy_amount = round(buy_qty * current_price, 2)
            if buy_amount > cash:
                buy_qty = int(cash / current_price / 100) * 100
                if buy_qty <= 0:
                    continue
                buy_amount = round(buy_qty * current_price, 2)

            priority_counter += 1
            decisions.append(Decision(
                ts_code=sig.ts_code,
                name=sig.name,
                industry=ind,
                action="BUY",
                quantity=buy_qty,
                reason_code="add_position",
                reason_text=(
                    f"分批加仓: 较均价已跌{actual_drop_pct:.1f}%≥{required_drop_pct:.0f}%"
                    f"（PB={sig.pb:.2f}，{'偏高' if sig.pb > high_pb_threshold else '偏低'}入场规则）"
                ),
                current_price=current_price,
                target_amount=buy_amount,
                industry_position_pct=round(ind_pct, 2),
                total_position_pct=round(total_position_pct, 2),
                explain={
                    **sig.explain,
                    "cost_price": cost_price,
                    "actual_drop_pct": round(actual_drop_pct, 2),
                    "required_drop_pct": required_drop_pct,
                    "buy_budget": round(buy_budget, 2),
                    "buy_qty": buy_qty,
                },
                priority=priority_counter,
            ))
            # ── 实时扣减：让后续加仓信号看到准确的现金/仓位约束 ──
            cash -= buy_amount
            industry_mv[ind] = industry_mv.get(ind, 0) + buy_amount
            total_mv = sum(industry_mv.values())
            total_position_pct = (total_mv / total_assets * 100) if total_assets > 0 else 0

    # Sort: SELL first, then BUY by priority (score order), then NO_ACTION
    decisions.sort(key=lambda d: (
        0 if d.action == "SELL" else (1 if d.action == "BUY" else 2),
        -d.priority,
    ))

    return decisions


# ===================== 第三层: 编排入口 =====================

def run_strategy(
    strategy_id: str = "dashu_v1",
    account_id: str = "",
    run_date: str = "",
    project_root: str = "",
) -> StrategyRunResult:
    """
    Full orchestration: load config, gather data, generate signals,
    make decisions, and persist results to DB.

    Args:
        strategy_id: Strategy identifier
        account_id: Account to run against
        run_date: Date to run for (default: today)
        project_root: Project root directory

    Returns:
        StrategyRunResult with signals and decisions
    """
    if not run_date:
        run_date = sh_date_str()

    if not project_root:
        project_root = str(Path(__file__).resolve().parent.parent.parent)

    result = StrategyRunResult(strategy_id=strategy_id, run_date=run_date)

    user_db_path = os.path.join(project_root, "user_data.db")
    market_db_path = os.path.join(project_root, "market.db")

    try:
        # 1. Load strategy config
        conn_user = sqlite3.connect(user_db_path)
        conn_user.row_factory = sqlite3.Row
        c = conn_user.cursor()

        c.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
        strategy_row = c.fetchone()
        if not strategy_row:
            result.error = f"Strategy '{strategy_id}' not found"
            conn_user.close()
            return result

        config_path = os.path.join(project_root, strategy_row["config_path"])
        if not os.path.exists(config_path):
            result.error = f"Config file not found: {config_path}"
            conn_user.close()
            return result

        strategy_config = load_strategy_config(config_path)

        # 2. Gather market data from stock_pool
        c.execute("SELECT * FROM stock_pool")
        pool_rows = c.fetchall()
        market_data = [dict(r) for r in pool_rows]

        if not market_data:
            result.error = "stock_pool is empty. Run scoring first."
            conn_user.close()
            return result

        # 3. Get current holdings for the account
        holdings = []
        holdings_map = {}
        if account_id:
            c.execute("""
                SELECT h.ts_code, h.name, h.quantity, h.cost_price, h.total_cost,
                       h.buy_pb,
                       si.anchor_industry as industry
                FROM holdings h
                LEFT JOIN stock_industry si ON h.ts_code = si.ts_code
                WHERE h.account_id = ? AND h.quantity > 0
            """, (account_id,))
            for row in c.fetchall():
                h = dict(row)
                holdings.append(h)
                holdings_map[h["ts_code"]] = h

        # 3.5 从 market.db 拉取收盘价和一年低点数据
        pool_codes = [r["ts_code"] for r in market_data]
        holding_codes = [h["ts_code"] for h in holdings]
        all_codes = list(set(pool_codes + holding_codes))
        price_data = _fetch_market_prices(market_db_path, all_codes)

        # 注入 close_price 和 rise_from_1y_low_pct 到 market_data
        for row in market_data:
            pinfo = price_data.get(row["ts_code"], {})
            row["close_price"] = pinfo.get("close_price")
            row["rise_from_1y_low_pct"] = pinfo.get("rise_from_1y_low_pct")

        # 用真实收盘价计算 holdings 的 market_value 和 current_price
        for h in holdings:
            pinfo = price_data.get(h["ts_code"], {})
            close = pinfo.get("close_price")
            if close and close > 0:
                h["current_price"] = close
                h["market_value"] = h["quantity"] * close
            else:
                # 无行情数据时保守用成本价，并记录来源
                h["current_price"] = h["cost_price"]
                h["market_value"] = h["quantity"] * h["cost_price"]
                logger.warning(f"持仓 {h['ts_code']} 无最新收盘价，用成本价估算市值")

        # 4. Get account info
        account_info = {"cash_balance": 0, "total_assets": 0}
        if account_id:
            c.execute("SELECT cash_balance FROM accounts WHERE id = ?", (account_id,))
            acc_row = c.fetchone()
            if acc_row:
                account_info["cash_balance"] = float(acc_row["cash_balance"])
                total_mv = sum(h.get("market_value", 0) for h in holdings)
                account_info["total_assets"] = account_info["cash_balance"] + total_mv

        # 5. Get market-wide PB (if available from market.db)
        market_pb = None
        if os.path.exists(market_db_path):
            try:
                conn_market = sqlite3.connect(market_db_path)
                conn_market.row_factory = sqlite3.Row
                cm = conn_market.cursor()
                cm.execute("""
                    SELECT pb_close FROM market_monthly
                    ORDER BY year_month DESC LIMIT 1
                """)
                row = cm.fetchone()
                if row:
                    market_pb = _to_float(row["pb_close"])
                conn_market.close()
            except Exception as e:
                logger.warning(f"Failed to get market PB: {e}")

        # 6. Generate signals
        signals = generate_signals(
            strategy_config=strategy_config,
            market_data=market_data,
            holdings_map=holdings_map,
            market_pb=market_pb,
            run_date=run_date,
        )

        # 7. Make decisions
        decisions = make_decisions(
            strategy_config=strategy_config,
            signals=signals,
            holdings=holdings,
            account=account_info,
            run_date=run_date,
        )

        result.signals = signals
        result.decisions = decisions

        # 8. Persist to DB
        _persist_run(conn_user, strategy_id, account_id, run_date, signals, decisions, result)

        conn_user.close()

    except Exception as e:
        logger.error(f"Strategy run failed: {e}", exc_info=True)
        result.error = str(e)

    return result


def _persist_run(
    conn: sqlite3.Connection,
    strategy_id: str,
    account_id: str,
    run_date: str,
    signals: List[Signal],
    decisions: List[Decision],
    result: StrategyRunResult,
):
    """Persist signals and decisions to the database."""
    c = conn.cursor()
    acc = account_id or None  # 空字符串统一存 NULL

    # Create run record
    now_ts = sh_timestamp_str()
    c.execute("""
        INSERT INTO strategy_runs (strategy_id, account_id, run_date, run_type, signal_count, decision_count, status, created_at)
        VALUES (?, ?, ?, 'manual', ?, ?, 'completed', ?)
    """, (strategy_id, acc, run_date, len(signals), len(decisions), now_ts))
    run_id = c.lastrowid
    result.run_id = run_id

    # Insert signals (only non-SKIP to save space)
    sig_records = [
        (run_id, strategy_id, acc, run_date, s.ts_code, s.name, s.industry,
         s.signal, s.score, s.pb, s.pe_ttm, s.pb_anchor, s.pb_ratio,
         s.rise_from_1y_low_pct, s.reason_code, s.reason_text,
         json.dumps(s.explain, ensure_ascii=False, default=str), now_ts)
        for s in signals if s.signal != "SKIP"
    ]
    c.executemany("""
        INSERT INTO strategy_signals
        (run_id, strategy_id, account_id, run_date, ts_code, name, industry,
         signal, score, pb, pe_ttm, pb_anchor, pb_ratio,
         rise_from_1y_low_pct, reason_code, reason_text, explain_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sig_records)

    # Insert decisions
    dec_records = [
        (run_id, strategy_id, acc, run_date, d.ts_code, d.name, d.industry,
         d.action, d.quantity, d.reason_code, d.reason_text, d.blocked_by,
         d.current_price, d.target_amount, d.industry_position_pct,
         d.total_position_pct,
         json.dumps(d.explain, ensure_ascii=False, default=str),
         d.priority, now_ts)
        for d in decisions
    ]
    c.executemany("""
        INSERT INTO strategy_decisions
        (run_id, strategy_id, account_id, run_date, ts_code, name, industry,
         action, quantity, reason_code, reason_text, blocked_by,
         current_price, target_amount, industry_position_pct,
         total_position_pct, explain_json, priority, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, dec_records)

    conn.commit()
    logger.info(
        f"Strategy run saved: run_id={run_id}, "
        f"signals={len(sig_records)}, decisions={len(dec_records)}"
    )
