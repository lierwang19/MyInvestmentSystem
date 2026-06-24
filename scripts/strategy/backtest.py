# -*- coding: utf-8 -*-
"""
Backtest engine for dashu_v1 strategy.

Simulates monthly rebalancing using historical market data.

Key constraints:
- Uses daily_valuation table for pb/pe (only ~200 stocks, 2024-2025)
- Uses daily_price for close prices
- Rebalances on the last trading day of each month
"""

import os
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

COMMISSION_RATE = 0.0003  # 0.03%
STAMP_TAX_RATE = 0.001    # 0.1% (sell only)
MIN_COMMISSION = 5.0      # 5 yuan minimum per trade


@dataclass
class BTHolding:
    ts_code: str
    name: str = ""
    industry: str = ""
    quantity: int = 0
    avg_cost: float = 0.0
    total_cost: float = 0.0
    batch_count: int = 0
    buy_pb: float = 0.0  # PB at first-buy time, for from_buy_pb sell matching


@dataclass
class BTState:
    cash: float
    holdings: Dict[str, BTHolding] = field(default_factory=dict)

    def holding_cost(self) -> float:
        return sum(h.total_cost for h in self.holdings.values())

    def market_value(self, prices: Dict[str, float]) -> float:
        return sum(prices.get(code, h.avg_cost) * h.quantity
                   for code, h in self.holdings.items())

    def total_nav(self, prices: Dict[str, float]) -> float:
        return self.cash + self.market_value(prices)


@dataclass
class BacktestResult:
    backtest_id: int
    nav_records: List[Dict]
    trade_records: List[Dict]
    final_nav: float
    return_pct: float
    max_drawdown_pct: float
    error: str = ""


# ── Data helpers ──

def _get_trading_dates(market_db: str, start_date: str, end_date: str) -> List[str]:
    conn = sqlite3.connect(market_db)
    c = conn.cursor()
    c.execute(
        "SELECT DISTINCT trade_date FROM daily_price "
        "WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
        (start_date, end_date),
    )
    dates = [r[0] for r in c.fetchall()]
    conn.close()
    return dates


def _get_monthly_rebalance_dates(trading_dates: List[str]) -> List[str]:
    """Return the last trading day of each month."""
    monthly: Dict[str, str] = {}
    for d in trading_dates:
        ym = d[:7]
        monthly[ym] = d  # later date overwrites earlier
    return sorted(monthly.values())


def _fetch_snapshot(market_db: str, as_of_date: str) -> List[Dict]:
    """
    Fetch latest fundamental + price data as of as_of_date.
    Returns list of dicts: ts_code, pb, pe_ttm, close_price, min_1y_low
    """
    conn = sqlite3.connect(market_db)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Find the most recent valuation date on or before as_of_date
    c.execute(
        "SELECT MAX(trade_date) FROM daily_valuation WHERE trade_date <= ?",
        (as_of_date,),
    )
    row = c.fetchone()
    latest_val_date = row[0] if row and row[0] else None

    if not latest_val_date:
        conn.close()
        return []

    # Use latest_val_date as the consistent price/valuation anchor.
    # Both PB/PE and close_price come from the same date to avoid mixed snapshots.
    one_year_ago = (
        datetime.strptime(latest_val_date, "%Y-%m-%d") - timedelta(days=365)
    ).strftime("%Y-%m-%d")

    c.execute(
        """
        SELECT v.ts_code,
               v.pb,
               v.pe_ttm,
               p.close  AS close_price,
               lows.min_1y_low
        FROM daily_valuation v
        LEFT JOIN daily_price p
               ON v.ts_code = p.ts_code AND p.trade_date = ?
        LEFT JOIN (
            SELECT ts_code, MIN(low) AS min_1y_low
            FROM daily_price
            WHERE trade_date >= ? AND trade_date <= ?
            GROUP BY ts_code
        ) lows ON v.ts_code = lows.ts_code
        WHERE v.trade_date = ?
        """,
        (latest_val_date, one_year_ago, latest_val_date, latest_val_date),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _fetch_industry(user_db: str, ts_codes: List[str]) -> Dict[str, Dict]:
    """Return {ts_code: {anchor_industry, tdx_l1, name}} from stock_industry.

    anchor_industry is used for PB anchor lookup (matches scoring.yaml
    industry_field_for_anchor: anchor_industry). tdx_l1 is kept for display.
    """
    if not ts_codes:
        return {}
    conn = sqlite3.connect(user_db)
    c = conn.cursor()
    placeholders = ",".join("?" * len(ts_codes))
    c.execute(
        f"SELECT ts_code, anchor_industry, tdx_l1, name "
        f"FROM stock_industry WHERE ts_code IN ({placeholders})",
        ts_codes,
    )
    result = {
        r[0]: {
            "industry": r[1] or r[2] or "",   # anchor_industry for anchor lookup
            "tdx_l1": r[2] or "",              # l1 for display/position grouping
            "name": r[3] or "",
        }
        for r in c.fetchall()
    }
    conn.close()
    return result


# ── Config helper ──

def _parse_config(strategy_config: Dict, project_root: str = "") -> Dict:
    """
    Flatten the nested YAML structure (dashu_v1.yaml) into a single dict of
    params used by _compute_signals / _apply_decisions.

    Also loads per-industry PB anchors from config/scoring.yaml.
    """
    import yaml as _yaml

    buy_rules = strategy_config.get("buy_rules", {})
    sell_rules = strategy_config.get("sell_rules", {})
    entry = buy_rules.get("entry", {})
    batch = buy_rules.get("batch", {})
    limits = buy_rules.get("position_limits", {})
    sell_method = sell_rules.get("method", "from_1y_low")
    sell_thresholds = sell_rules.get(sell_method, {}).get("thresholds", [])

    # Use first (lowest) threshold's rise_pct as the SELL_CANDIDATE trigger;
    # keep the full list so _apply_decisions can match the correct sell_ratio.
    sell_rise_pct = float(sell_thresholds[0].get("rise_pct", 100)) if sell_thresholds else 100.0

    # ── Fix 3: Load per-industry PB anchors from scoring.yaml ──
    anchors: Dict[str, float] = {}
    default_anchor = 1.0
    if project_root:
        scoring_path = os.path.join(project_root, "config", "scoring.yaml")
        if os.path.exists(scoring_path):
            try:
                with open(scoring_path, "r", encoding="utf-8") as f:
                    scoring_cfg = _yaml.safe_load(f) or {}
                pb_cfg = scoring_cfg.get("positives", {}).get("pb", {})
                default_anchor = float(pb_cfg.get("anchor_default", 1.0))
                raw_anchors = pb_cfg.get("anchors_by_industry", {})
                anchors = {str(k): float(v) for k, v in raw_anchors.items()}
                logger.info(f"Loaded {len(anchors)} industry PB anchors from scoring.yaml (default={default_anchor})")
            except Exception as e:
                logger.warning(f"Failed to load scoring.yaml anchors: {e}")

    return {
        "buy_pb_max": float(entry.get("max_pb", 2.0)),
        "buy_pe_max": float(entry.get("max_pe", 20.0)),
        "market_pb_halt": float(entry.get("market_pb_halt", 2.5)),
        "sell_method": sell_method,
        "sell_rise_pct": sell_rise_pct,
        "sell_thresholds": sell_thresholds,  # full list for ratio matching
        "anchors_by_industry": anchors,
        "default_pb_anchor": default_anchor,
        "high_pb_threshold": float(batch.get("high_pb_threshold", 1.5)),
        "high_pb_drop_pct": float(batch.get("high_pb_drop_pct", 20.0)),
        "low_pb_drop_pct": float(batch.get("low_pb_drop_pct", 10.0)),
        "max_industry_pct": float(limits.get("max_industry_pct", 3.0)),
        "max_total_pct": float(limits.get("max_total_position_pct", 66.7)),
        # per-stock target matches per-industry cap (one stock per industry)
        "per_stock_pct": float(limits.get("max_industry_pct", 3.0)),
        # max_stocks: strategy aims for min_portfolio_count, cap buy loop at 2×
        "max_stocks": int(limits.get("min_portfolio_count", 30)) * 2,
    }


# ── Strategy logic ──

def _compute_signals(
    snapshot: List[Dict],
    holdings: Dict[str, BTHolding],
    industry_map: Dict[str, Dict],
    params: Dict,
    market_pb: Optional[float] = None,
) -> List[Dict]:
    signals = []

    sell_rise_pct = params["sell_rise_pct"]
    sell_thresholds = params.get("sell_thresholds", [])
    anchors = params["anchors_by_industry"]
    default_anchor = params["default_pb_anchor"]
    buy_pb_max = params["buy_pb_max"]
    buy_pe_max = params.get("buy_pe_max", 20.0)
    market_pb_halt = params.get("market_pb_halt", 2.5)

    # ── Fix 2: Market PB halt check ──
    market_halted = False
    if market_pb is not None and market_pb > market_pb_halt:
        market_halted = True
        logger.info(f"Market PB {market_pb:.2f} > halt line {market_pb_halt}, buys suspended")

    for row in snapshot:
        ts_code = row["ts_code"]
        pb = row.get("pb")
        pe_ttm = row.get("pe_ttm")
        close_price = row.get("close_price")
        min_1y_low = row.get("min_1y_low")

        if not pb or pb <= 0:
            continue
        if not close_price or close_price <= 0:
            continue

        info = industry_map.get(ts_code, {})
        industry = info.get("industry", "")
        name = info.get("name", "")

        # ── Fix 3: Simplified anchor lookup (anchors is now {industry: float}) ──
        anchor = anchors.get(industry, default_anchor)
        pb_ratio = pb / anchor if anchor and anchor > 0 else None

        rise_pct = None
        if min_1y_low and min_1y_low > 0 and close_price > 0:
            rise_pct = (close_price - min_1y_low) / min_1y_low * 100

        is_held = ts_code in holdings and holdings[ts_code].quantity > 0

        sell_method = params.get("sell_method", "from_1y_low")
        # ── Fix 1: Attach sell_ratio to SELL_CANDIDATE signal ──
        sell_ratio = 0.5  # default
        if is_held:
            holding_buy_pb = holdings[ts_code].buy_pb if is_held else pb or 0.0
            matched_th = None
            if sell_method == "from_buy_pb":
                # Match by buy_pb_max bucket (ascending = strictest match first)
                for th in sorted(sell_thresholds, key=lambda t: float(t.get("buy_pb_max", 99))):
                    if holding_buy_pb <= float(th.get("buy_pb_max", 99)):
                        if rise_pct is not None and rise_pct >= float(th.get("rise_pct", 100)):
                            matched_th = th
                        break  # found the applicable bucket
            else:
                # from_1y_low: first threshold whose rise_pct is met
                if rise_pct is not None and rise_pct >= sell_rise_pct:
                    for th in sell_thresholds:
                        th_pct = float(th.get("rise_pct", 100))
                        if rise_pct >= th_pct:
                            matched_th = th

            if matched_th is not None:
                sell_ratio = float(matched_th.get("sell_ratio", 0.5))
                sig = "SELL_CANDIDATE"
                reason = (
                    f"PB桶≤{matched_th.get('buy_pb_max')},涨幅{rise_pct:.1f}%≥{matched_th.get('rise_pct')}%"
                    if sell_method == "from_buy_pb"
                    else f"离低点{rise_pct:.1f}%≥{sell_rise_pct}%"
                )
            else:
                sig, reason = "HOLD", "持有中"
                sell_ratio = 0
        else:
            sell_ratio = 0
            # ── Fix 2: PE check + market PB halt ──
            if market_halted:
                sig = "SKIP"
                reason = f"市场PB={market_pb:.2f}>暂停线{market_pb_halt}"
            elif not pe_ttm or pe_ttm <= 0 or pe_ttm >= buy_pe_max:
                sig = "SKIP"
                reason = f"PE={'N/A' if not pe_ttm else f'{pe_ttm:.1f}'}不满足(<{buy_pe_max})"
            elif pb_ratio and pb_ratio <= 1.0 and pb <= buy_pb_max:
                sig = "BUY_CANDIDATE"
                reason = f"PB={pb:.2f},PB比={pb_ratio:.2f},PE={pe_ttm:.1f}"
            else:
                sig = "SKIP"
                reason = f"PB={pb:.2f}不满足买入"

        score = round(100 * (1 - (pb_ratio or 1.0)), 1) if pb_ratio else 0

        sig_dict: Dict[str, Any] = {
            "ts_code": ts_code,
            "name": name,
            "industry": industry,
            "signal": sig,
            "score": score,
            "pb": pb,
            "pe_ttm": pe_ttm,
            "pb_anchor": anchor,
            "pb_ratio": pb_ratio,
            "close_price": close_price,
            "min_1y_low": min_1y_low,
            "rise_from_1y_low_pct": rise_pct,
            "reason": reason,
        }
        if sig == "SELL_CANDIDATE":
            sig_dict["sell_ratio"] = sell_ratio
        signals.append(sig_dict)

    return signals


def _apply_decisions(
    state: BTState,
    signals: List[Dict],
    date: str,
    params: Dict,
    trade_records: List[Dict],
    prices: Dict[str, float],
) -> None:
    """Apply buy/sell/add-position decisions to state in-place.

    All position limit checks use current market value (not historical cost)
    so that unrealised gains/losses are properly reflected in the constraints.
    """

    max_industry_pct = params["max_industry_pct"]
    max_total_pct = params["max_total_pct"]
    max_stocks = params["max_stocks"]
    per_stock_pct = params["per_stock_pct"]
    high_pb_threshold = params["high_pb_threshold"]
    high_pb_drop_pct = params["high_pb_drop_pct"]
    low_pb_drop_pct = params["low_pb_drop_pct"]

    def _total_assets() -> float:
        return state.total_nav(prices)

    def _holding_mkt_val(code: str, holding: "BTHolding") -> float:
        return prices.get(code, holding.avg_cost) * holding.quantity

    # ── 1. Sells (Fix 1: sell by ratio, not full liquidation) ──
    for sig in [s for s in signals if s["signal"] == "SELL_CANDIDATE"]:
        code = sig["ts_code"]
        if code not in state.holdings or state.holdings[code].quantity <= 0:
            continue
        h = state.holdings[code]
        price = sig["close_price"]

        # Calculate sell quantity based on sell_ratio from signal
        sell_ratio = sig.get("sell_ratio", 0.5)
        sell_qty = (int(h.quantity * sell_ratio) // 100) * 100  # sell_ratio is 0~1; round down to lots of 100
        if sell_qty <= 0:
            sell_qty = h.quantity  # less than 100 shares → sell all
        if sell_qty > h.quantity:
            sell_qty = h.quantity

        gross = price * sell_qty
        tax = round(gross * STAMP_TAX_RATE, 2)
        comm = max(round(gross * COMMISSION_RATE, 2), MIN_COMMISSION)
        state.cash += gross - tax - comm

        # Update or remove holding
        if sell_qty >= h.quantity:
            del state.holdings[code]
        else:
            h.quantity -= sell_qty
            h.total_cost = h.avg_cost * h.quantity

        trade_records.append({
            "date": date, "ts_code": code, "name": sig["name"],
            "action": "SELL", "quantity": sell_qty, "price": price,
            "amount": gross, "commission": comm, "stamp_tax": tax,
            "reason_text": sig["reason"],
        })

    # ── 2. Buys (sorted by score desc) ──
    buy_sigs = sorted(
        [s for s in signals if s["signal"] == "BUY_CANDIDATE"],
        key=lambda x: x.get("score", 0), reverse=True,
    )
    for sig in buy_sigs:
        if len(state.holdings) >= max_stocks:
            break
        code = sig["ts_code"]
        if code in state.holdings:
            continue
        price = sig["close_price"]
        total_assets = _total_assets()
        target = total_assets * (per_stock_pct / 100.0)

        # Industry cap: use current market value of same-industry holdings
        industry = sig["industry"]
        ind_mkt = sum(
            _holding_mkt_val(c, h)
            for c, h in state.holdings.items() if h.industry == industry
        )
        if total_assets > 0 and (ind_mkt + target) / total_assets * 100 > max_industry_pct:
            continue

        # Total position cap: use current market value
        pos_mkt = state.market_value(prices)
        if total_assets > 0 and (pos_mkt + target) / total_assets * 100 > max_total_pct:
            break

        qty = int(target / price / 100) * 100
        if qty <= 0:
            continue
        gross = qty * price
        comm = max(round(gross * COMMISSION_RATE, 2), MIN_COMMISSION)
        if state.cash < gross + comm:
            continue

        state.cash -= gross + comm
        state.holdings[code] = BTHolding(
            ts_code=code, name=sig["name"], industry=industry,
            quantity=qty, avg_cost=price, total_cost=gross, batch_count=1,
            buy_pb=float(sig.get("pb") or 0.0),  # record PB at buy time for from_buy_pb matching
        )
        trade_records.append({
            "date": date, "ts_code": code, "name": sig["name"],
            "action": "BUY", "quantity": qty, "price": price,
            "amount": gross, "commission": comm, "stamp_tax": 0,
            "reason_text": sig["reason"],
        })

    # ── 3. Add-position for HOLD ──
    for sig in [s for s in signals if s["signal"] == "HOLD"]:
        code = sig["ts_code"]
        if code not in state.holdings or state.holdings[code].quantity <= 0:
            continue
        h = state.holdings[code]
        price = sig["close_price"]
        pb = sig.get("pb") or 0
        required_drop = high_pb_drop_pct if pb > high_pb_threshold else low_pb_drop_pct
        actual_drop = (h.avg_cost - price) / h.avg_cost * 100 if h.avg_cost > 0 else 0
        if actual_drop < required_drop:
            continue

        total_assets = _total_assets()
        target = total_assets * (per_stock_pct / 100.0)

        # Industry cap check (same logic as first-buy)
        ind_mkt = sum(
            _holding_mkt_val(c, hh)
            for c, hh in state.holdings.items() if hh.industry == h.industry
        )
        if total_assets > 0 and (ind_mkt + target) / total_assets * 100 > max_industry_pct:
            continue

        # Total position cap check
        pos_mkt = state.market_value(prices)
        if total_assets > 0 and (pos_mkt + target) / total_assets * 100 > max_total_pct:
            continue

        qty = int(target / price / 100) * 100
        if qty <= 0:
            continue
        gross = qty * price
        comm = max(round(gross * COMMISSION_RATE, 2), MIN_COMMISSION)
        if state.cash < gross + comm:
            continue

        state.cash -= gross + comm
        new_qty = h.quantity + qty
        h.avg_cost = (h.total_cost + gross) / new_qty
        h.total_cost += gross
        h.quantity = new_qty
        h.batch_count += 1
        trade_records.append({
            "date": date, "ts_code": code, "name": sig["name"],
            "action": "BUY", "quantity": qty, "price": price,
            "amount": gross, "commission": comm, "stamp_tax": 0,
            "reason_text": f"加仓: 跌{actual_drop:.1f}%≥{required_drop}%",
        })


def _max_drawdown(navs: List[float]) -> float:
    if len(navs) < 2:
        return 0.0
    peak, max_dd = navs[0], 0.0
    for nav in navs:
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


# ── Market PB helper ──

def _fetch_market_pb(market_db: str, as_of_date: str) -> Optional[float]:
    """Fetch the latest market-wide PB from market_monthly on or before as_of_date."""
    try:
        conn = sqlite3.connect(market_db)
        c = conn.cursor()
        ym = as_of_date[:7]  # YYYY-MM
        c.execute(
            "SELECT pb_close FROM market_monthly WHERE year_month <= ? ORDER BY year_month DESC LIMIT 1",
            (ym,),
        )
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            return float(row[0])
    except Exception as e:
        logger.warning(f"_fetch_market_pb failed for {as_of_date}: {e}")
    return None


# ── Main entry point ──

def run_backtest(
    backtest_id: int,
    strategy_id: str,
    start_date: str,
    end_date: str,
    init_cash: float,
    market_db_path: str,
    user_db_path: str,
    project_root: str,
) -> BacktestResult:
    """
    Run a monthly-rebalancing backtest simulation.
    Returns BacktestResult with nav_records, trade_records, and summary metrics.
    """
    import yaml

    config_path = os.path.join(project_root, "config", "strategies", f"{strategy_id}.yaml")
    config: Dict = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    # Flatten nested YAML structure into params used by signal/decision functions
    params = _parse_config(config, project_root)
    logger.info(
        f"Backtest params: sell_rise={params['sell_rise_pct']}%, "
        f"buy_pe_max={params['buy_pe_max']}, "
        f"market_pb_halt={params['market_pb_halt']}, "
        f"industry_cap={params['max_industry_pct']}%, "
        f"total_cap={params['max_total_pct']}%, "
        f"per_stock={params['per_stock_pct']}%, "
        f"anchors={len(params['anchors_by_industry'])} industries"
    )

    trading_dates = _get_trading_dates(market_db_path, start_date, end_date)
    if not trading_dates:
        return BacktestResult(
            backtest_id=backtest_id,
            nav_records=[], trade_records=[],
            final_nav=init_cash, return_pct=0.0, max_drawdown_pct=0.0,
            error=f"No trading dates found between {start_date} and {end_date}",
        )

    rebalance_dates = _get_monthly_rebalance_dates(trading_dates)
    logger.info(
        f"Backtest {backtest_id}: {len(rebalance_dates)} rebalance dates "
        f"({start_date} → {end_date})"
    )

    state = BTState(cash=init_cash)
    nav_records: List[Dict] = []
    trade_records: List[Dict] = []

    for rebal_date in rebalance_dates:
        try:
            snapshot = _fetch_snapshot(market_db_path, rebal_date)
            if not snapshot:
                logger.warning(f"No snapshot data for {rebal_date}, skipping")
                continue

            ts_codes = [r["ts_code"] for r in snapshot]
            industry_map = _fetch_industry(user_db_path, ts_codes)

            # ── Fix 2: Fetch market PB for this month ──
            market_pb = _fetch_market_pb(market_db_path, rebal_date)

            prices = {
                r["ts_code"]: r["close_price"]
                for r in snapshot
                if r.get("close_price") and r["close_price"] > 0
            }
            signals = _compute_signals(snapshot, state.holdings, industry_map, params, market_pb)
            _apply_decisions(state, signals, rebal_date, params, trade_records, prices)
            mkt_val = state.market_value(prices)
            total_nav = state.cash + mkt_val
            pos_pct = round(mkt_val / total_nav * 100, 1) if total_nav > 0 else 0.0

            nav_records.append({
                "date": rebal_date,
                "total_nav": round(total_nav, 2),
                "cash": round(state.cash, 2),
                "market_value": round(mkt_val, 2),
                "position_pct": pos_pct,
                "holding_count": len(state.holdings),
            })
        except Exception as e:
            logger.error(f"Error on {rebal_date}: {e}", exc_info=True)
            continue

    if not nav_records:
        return BacktestResult(
            backtest_id=backtest_id,
            nav_records=[], trade_records=trade_records,
            final_nav=init_cash, return_pct=0.0, max_drawdown_pct=0.0,
            error="No NAV records generated (check market data availability)",
        )

    final_nav = nav_records[-1]["total_nav"]
    return_pct = round((final_nav - init_cash) / init_cash * 100, 2)
    max_dd = _max_drawdown([r["total_nav"] for r in nav_records])

    return BacktestResult(
        backtest_id=backtest_id,
        nav_records=nav_records,
        trade_records=trade_records,
        final_nav=final_nav,
        return_pct=return_pct,
        max_drawdown_pct=max_dd,
    )
