# -*- coding: utf-8 -*-
"""
策略引擎单元测试 — generate_signals() / make_decisions()
=========================================================
覆盖场景：
  1. 无持仓首次买入
  2. 持仓且未达卖出阈值 → HOLD
  3. 持仓且达到卖出阈值 → SELL_CANDIDATE → SELL 决策
  4. 行业仓位超上限 → NO_ACTION / blocked_by_industry
  5. 总仓位超上限 → NO_ACTION / blocked_by_position
  6. 现金不足 → NO_ACTION / blocked_by_cash
  7. 不满足 PB/PE 条件 → SKIP
  8. 无收盘价 → NO_ACTION / no_price
  9. 持仓且 rise_pct=None（新股/行情不足）→ HOLD / holding_no_rise_data，不产生 BUY
 10. HOLD + 分批加仓触发（偏低入场，跌幅 ≥ 10%）
 11. HOLD + 跌幅不足，不加仓
 12. HOLD + 偏高入场（PB > 1.5），需跌 20% 才加仓
 13. 市场 PB 过高 → 全部 SKIP
 14. 卖出整手规则（sell_qty 是 100 的整数倍）
"""

import sys
import os

# 让测试能直接 import engine 而不依赖安装
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'strategy'))

import pytest
from engine import generate_signals, make_decisions


# ─────────────────────────────────────────────────────────────
# 公共 fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def base_config():
    return {
        "buy_rules": {
            "entry": {
                "max_pb": 2.0,
                "max_pe": 20.0,
                "market_pb_halt": 2.5,
            },
            "batch": {
                "high_pb_threshold": 1.5,
                "high_pb_drop_pct": 20.0,
                "low_pb_drop_pct": 10.0,
            },
            "position_limits": {
                "max_industry_pct": 3.0,
                "max_total_position_pct": 66.7,
            },
        },
        "sell_rules": {
            "method": "from_1y_low",
            "from_1y_low": {
                "thresholds": [
                    {"rise_pct": 100, "sell_ratio": 0.5},
                    {"rise_pct": 200, "sell_ratio": 0.5},
                ]
            },
        },
    }


def make_stock(ts_code, industry="消费", pb=0.8, pe=8.0, anchor_pb=1.0,
               score=70.0, close_price=10.0, rise_pct=20.0, name=None):
    return {
        "ts_code": ts_code,
        "name": name or ts_code,
        "industry": industry,
        "pb": pb,
        "pe": pe,
        "anchor_pb": anchor_pb,
        "score": score,
        "close_price": close_price,
        "rise_from_1y_low_pct": rise_pct,
    }


def make_holding(ts_code, qty, cost_price, industry="消费", close_price=None):
    cp = close_price or cost_price
    return {
        "ts_code": ts_code,
        "name": ts_code,
        "industry": industry,
        "quantity": qty,
        "cost_price": cost_price,
        "market_value": qty * cp,
        "current_price": cp,
    }


def signals_map(signals):
    return {s.ts_code: s for s in signals}


def decisions_map(decisions):
    return {d.ts_code: d for d in decisions}


# ─────────────────────────────────────────────────────────────
# 1. 无持仓首次买入
# ─────────────────────────────────────────────────────────────

def test_first_buy(base_config):
    market_data = [make_stock("000001.SZ", close_price=10.0, pb=0.8, pe=8.0)]
    signals = generate_signals(base_config, market_data, {})
    sm = signals_map(signals)
    assert sm["000001.SZ"].signal == "BUY_CANDIDATE"

    account = {"cash_balance": 200_000, "total_assets": 200_000}
    decisions = make_decisions(base_config, signals, [], account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "BUY"
    assert d.reason_code == "first_buy"
    assert d.current_price == 10.0          # 使用收盘价，不是 PB 值
    assert d.quantity >= 100                 # 至少一手
    assert d.quantity % 100 == 0            # 整手


# ─────────────────────────────────────────────────────────────
# 2. 持仓且未达卖出阈值 → HOLD
# ─────────────────────────────────────────────────────────────

def test_hold_below_sell_threshold(base_config):
    market_data = [make_stock("000001.SZ", rise_pct=50.0)]   # 50% < 100%
    holdings_map = {"000001.SZ": {"quantity": 1000, "cost_price": 10.0}}
    signals = generate_signals(base_config, market_data, holdings_map)
    sm = signals_map(signals)
    assert sm["000001.SZ"].signal == "HOLD"
    assert sm["000001.SZ"].reason_code == "holding"


# ─────────────────────────────────────────────────────────────
# 3. 达到卖出阈值 → SELL_CANDIDATE → SELL 决策
# ─────────────────────────────────────────────────────────────

def test_sell_on_rise_threshold(base_config):
    market_data = [make_stock("000001.SZ", rise_pct=110.0, close_price=20.0)]
    holdings_map = {"000001.SZ": {"quantity": 1000, "cost_price": 10.0}}
    signals = generate_signals(base_config, market_data, holdings_map)
    sm = signals_map(signals)
    assert sm["000001.SZ"].signal == "SELL_CANDIDATE"

    holdings = [make_holding("000001.SZ", qty=1000, cost_price=10.0, close_price=20.0)]
    account = {"cash_balance": 50_000, "total_assets": 250_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "SELL"
    assert d.quantity >= 100
    assert d.quantity % 100 == 0
    assert d.current_price == 20.0


def test_sell_qty_multiple_of_100(base_config):
    """卖出股数必须是 100 的整数倍（非全仓时）"""
    market_data = [make_stock("000001.SZ", rise_pct=110.0, close_price=15.0)]
    holdings_map = {"000001.SZ": {"quantity": 300, "cost_price": 10.0}}
    signals = generate_signals(base_config, market_data, holdings_map)
    holdings = [make_holding("000001.SZ", qty=300, cost_price=10.0, close_price=15.0)]
    account = {"cash_balance": 10_000, "total_assets": 50_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "SELL"
    # 卖出数量要么是整手，要么是全仓清空
    if d.quantity < 300:
        assert d.quantity % 100 == 0
    else:
        assert d.quantity == 300


# ─────────────────────────────────────────────────────────────
# 4. 行业仓位超上限 → NO_ACTION / blocked_by_industry
# ─────────────────────────────────────────────────────────────

def test_blocked_by_industry_limit(base_config):
    # 总资产 100 万，同行业持仓 3.5 万（> 3% = 3万）
    total_assets = 1_000_000
    market_data = [make_stock("000001.SZ", industry="银行", close_price=10.0)]
    signals = generate_signals(base_config, market_data, {})
    assert signals_map(signals)["000001.SZ"].signal == "BUY_CANDIDATE"

    # 银行行业已持仓 35000（3.5% > 3%）
    holdings = [make_holding("600000.SH", qty=1000, cost_price=35.0, industry="银行", close_price=35.0)]
    account = {"cash_balance": 500_000, "total_assets": total_assets}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "NO_ACTION"
    assert d.blocked_by == "industry_limit"


# ─────────────────────────────────────────────────────────────
# 5. 总仓位超上限 → NO_ACTION / blocked_by_position
# ─────────────────────────────────────────────────────────────

def test_blocked_by_total_position(base_config):
    # 总资产 100 万，持仓 70 万（70% > 66.7%）
    total_assets = 1_000_000
    market_data = [make_stock("000001.SZ", industry="消费", close_price=10.0)]
    signals = generate_signals(base_config, market_data, {})

    # 用一只不同行业的大仓位持仓撑高总仓位
    holdings = [make_holding("600036.SH", qty=10000, cost_price=70.0, industry="银行", close_price=70.0)]
    account = {"cash_balance": 300_000, "total_assets": total_assets}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "NO_ACTION"
    assert d.blocked_by == "position_limit"


# ─────────────────────────────────────────────────────────────
# 6. 现金不足 → NO_ACTION / blocked_by_cash
# ─────────────────────────────────────────────────────────────

def test_blocked_by_cash(base_config):
    market_data = [make_stock("000001.SZ", close_price=10.0)]
    signals = generate_signals(base_config, market_data, {})
    account = {"cash_balance": 0, "total_assets": 100_000}
    decisions = make_decisions(base_config, signals, [], account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "NO_ACTION"
    assert d.blocked_by == "cash"


def test_blocked_by_cash_less_than_one_lot(base_config):
    """现金够但不足一手（price=100, 100手=10000元，现金只有5000）"""
    market_data = [make_stock("000001.SZ", close_price=100.0)]
    signals = generate_signals(base_config, market_data, {})
    account = {"cash_balance": 5_000, "total_assets": 100_000}
    decisions = make_decisions(base_config, signals, [], account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "NO_ACTION"
    assert d.blocked_by == "cash"


# ─────────────────────────────────────────────────────────────
# 7. 不满足 PB/PE 条件 → SKIP
# ─────────────────────────────────────────────────────────────

def test_skip_high_pb(base_config):
    market_data = [make_stock("000001.SZ", pb=3.0, pe=8.0)]    # PB > 2.0
    signals = generate_signals(base_config, market_data, {})
    assert signals_map(signals)["000001.SZ"].signal == "SKIP"


def test_skip_high_pe(base_config):
    market_data = [make_stock("000001.SZ", pb=0.8, pe=25.0)]   # PE > 20.0
    signals = generate_signals(base_config, market_data, {})
    assert signals_map(signals)["000001.SZ"].signal == "SKIP"


def test_skip_both_high(base_config):
    market_data = [make_stock("000001.SZ", pb=3.0, pe=30.0)]
    signals = generate_signals(base_config, market_data, {})
    assert signals_map(signals)["000001.SZ"].signal == "SKIP"


# ─────────────────────────────────────────────────────────────
# 8. 无收盘价 → NO_ACTION / no_price
# ─────────────────────────────────────────────────────────────

def test_no_price_buy_candidate(base_config):
    market_data = [make_stock("000001.SZ", pb=0.8, pe=8.0, close_price=None)]
    signals = generate_signals(base_config, market_data, {})
    # 信号层面仍是 BUY_CANDIDATE（缺价格不影响信号资格）
    assert signals_map(signals)["000001.SZ"].signal == "BUY_CANDIDATE"

    account = {"cash_balance": 200_000, "total_assets": 200_000}
    decisions = make_decisions(base_config, signals, [], account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "NO_ACTION"
    assert d.reason_code == "no_price"


# ─────────────────────────────────────────────────────────────
# 9. 持仓且 rise_pct=None → HOLD/holding_no_rise_data，不产生 BUY
# ─────────────────────────────────────────────────────────────

def test_holding_with_no_rise_data_stays_hold(base_config):
    """新股或行情不足时，持仓不应掉入 BUY_CANDIDATE 逻辑"""
    market_data = [make_stock("688001.SH", pb=0.9, pe=8.0,
                               close_price=50.0, rise_pct=None)]
    holdings_map = {"688001.SH": {"quantity": 100, "cost_price": 52.0}}
    signals = generate_signals(base_config, market_data, holdings_map)
    sm = signals_map(signals)

    assert sm["688001.SH"].signal == "HOLD"
    assert sm["688001.SH"].reason_code == "holding_no_rise_data"

    # 决策层：HOLD 信号不满足加仓条件（close < cost，但此处 50 < 52 跌幅仅 3.8% < 10%）
    holdings = [make_holding("688001.SH", qty=100, cost_price=52.0, close_price=50.0)]
    account = {"cash_balance": 500_000, "total_assets": 1_000_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)
    # 不应产生 BUY 建议
    assert "688001.SH" not in dm or dm["688001.SH"].action != "BUY"


# ─────────────────────────────────────────────────────────────
# 10. HOLD + 分批加仓触发（偏低入场 PB ≤ 1.5，跌幅 ≥ 10%）
# ─────────────────────────────────────────────────────────────

def test_add_position_low_pb_entry(base_config):
    """PB=1.2 (≤1.5 偏低入场)，当前价比均价跌 15% → 应加仓"""
    market_data = [make_stock("600036.SH", industry="工业",
                               pb=1.2, pe=7.0, close_price=42.5, rise_pct=5.0)]
    holdings_map = {"600036.SH": {"quantity": 1000, "cost_price": 50.0}}
    signals = generate_signals(base_config, market_data, holdings_map)
    assert signals_map(signals)["600036.SH"].signal == "HOLD"

    holdings = [make_holding("600036.SH", qty=1000, cost_price=50.0,
                              industry="工业", close_price=42.5)]
    account = {"cash_balance": 1_000_000, "total_assets": 2_000_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    d = dm["600036.SH"]
    assert d.action == "BUY"
    assert d.reason_code == "add_position"
    assert d.current_price == 42.5
    assert d.quantity % 100 == 0


# ─────────────────────────────────────────────────────────────
# 11. HOLD + 跌幅不足，不加仓
# ─────────────────────────────────────────────────────────────

def test_no_add_position_insufficient_drop(base_config):
    """PB=1.2 (偏低入场需 10%)，当前价比均价只跌 5% → 不加仓"""
    market_data = [make_stock("600036.SH", industry="工业",
                               pb=1.2, pe=7.0, close_price=47.5, rise_pct=10.0)]
    holdings_map = {"600036.SH": {"quantity": 1000, "cost_price": 50.0}}
    signals = generate_signals(base_config, market_data, holdings_map)

    holdings = [make_holding("600036.SH", qty=1000, cost_price=50.0,
                              industry="工业", close_price=47.5)]
    account = {"cash_balance": 1_000_000, "total_assets": 2_000_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    # 不应有 BUY 建议
    assert "600036.SH" not in dm or dm["600036.SH"].action != "BUY"


# ─────────────────────────────────────────────────────────────
# 12. HOLD + 偏高入场（PB > 1.5），需跌 20% 才加仓
# ─────────────────────────────────────────────────────────────

def test_add_position_high_pb_needs_20pct_drop(base_config):
    """PB=1.8 (>1.5 偏高入场需 20%)，跌 15% 不够 → 不加仓"""
    market_data = [make_stock("601318.SH", industry="保险",
                               pb=1.8, pe=12.0, close_price=42.5, rise_pct=10.0)]
    holdings_map = {"601318.SH": {"quantity": 500, "cost_price": 50.0}}
    signals = generate_signals(base_config, market_data, holdings_map)

    holdings = [make_holding("601318.SH", qty=500, cost_price=50.0,
                              industry="保险", close_price=42.5)]
    account = {"cash_balance": 1_000_000, "total_assets": 2_000_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)
    assert "601318.SH" not in dm or dm["601318.SH"].action != "BUY"


def test_add_position_high_pb_with_20pct_drop(base_config):
    """PB=1.8，跌 20% → 触发加仓"""
    market_data = [make_stock("601318.SH", industry="保险",
                               pb=1.8, pe=12.0, close_price=40.0, rise_pct=5.0)]
    holdings_map = {"601318.SH": {"quantity": 500, "cost_price": 50.0}}
    signals = generate_signals(base_config, market_data, holdings_map)

    holdings = [make_holding("601318.SH", qty=500, cost_price=50.0,
                              industry="保险", close_price=40.0)]
    account = {"cash_balance": 1_000_000, "total_assets": 2_000_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    d = dm["601318.SH"]
    assert d.action == "BUY"
    assert d.reason_code == "add_position"


# ─────────────────────────────────────────────────────────────
# 13. 市场 PB 过高 → 全部 SKIP
# ─────────────────────────────────────────────────────────────

def test_market_pb_halt_all_skip(base_config):
    """全市场 PB > 2.5 时所有非持仓股应 SKIP"""
    market_data = [
        make_stock("000001.SZ", pb=0.8, pe=8.0),
        make_stock("000002.SZ", pb=1.2, pe=10.0),
    ]
    signals = generate_signals(base_config, market_data, {}, market_pb=3.0)
    for s in signals:
        assert s.signal == "SKIP"
        assert s.reason_code == "market_pb_halt"


def test_market_pb_halt_does_not_affect_sell(base_config):
    """市场 PB 暂停只影响买入信号，持仓卖出信号不受影响"""
    market_data = [make_stock("000001.SZ", rise_pct=110.0, close_price=20.0)]
    holdings_map = {"000001.SZ": {"quantity": 1000, "cost_price": 10.0}}
    signals = generate_signals(base_config, market_data, holdings_map, market_pb=3.0)
    sm = signals_map(signals)
    # 持仓路径优先，不走市场暂停分支
    assert sm["000001.SZ"].signal == "SELL_CANDIDATE"


# ─────────────────────────────────────────────────────────────
# 14. 决策排序：SELL 在 BUY 之前
# ─────────────────────────────────────────────────────────────

def test_sell_sorted_before_buy(base_config):
    market_data = [
        make_stock("000001.SZ", rise_pct=110.0, close_price=20.0),   # 持仓卖出
        make_stock("000002.SZ", industry="地产", pb=0.8, pe=8.0, close_price=5.0),  # 新买入
    ]
    holdings_map = {"000001.SZ": {"quantity": 1000, "cost_price": 10.0}}
    signals = generate_signals(base_config, market_data, holdings_map)

    holdings = [make_holding("000001.SZ", qty=1000, cost_price=10.0, close_price=20.0)]
    account = {"cash_balance": 500_000, "total_assets": 1_000_000}
    decisions = make_decisions(base_config, signals, holdings, account)

    actions = [d.action for d in decisions if d.action in ("BUY", "SELL")]
    sell_idx = next(i for i, a in enumerate(actions) if a == "SELL")
    buy_idx  = next(i for i, a in enumerate(actions) if a == "BUY")
    assert sell_idx < buy_idx, "SELL 决策应排在 BUY 之前"


# ─────────────────────────────────────────────────────────────
# 15. sell_ratio=0.5 精度：1000 / 900 / 100 股三种持仓
# ─────────────────────────────────────────────────────────────

def test_sell_ratio_1000_shares(base_config):
    """1000 股 × sell_ratio=0.5 → 卖 500，保留 500（不是全仓清空）"""
    market_data = [make_stock("000001.SZ", rise_pct=110.0, close_price=20.0)]
    holdings_map = {"000001.SZ": {"quantity": 1000, "cost_price": 10.0}}
    signals = generate_signals(base_config, market_data, holdings_map)

    holdings = [make_holding("000001.SZ", qty=1000, cost_price=10.0, close_price=20.0)]
    account = {"cash_balance": 50_000, "total_assets": 250_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "SELL"
    assert d.quantity == 500, f"1000股 sell_ratio=0.5 应卖500股，实际卖了{d.quantity}股"


def test_sell_ratio_900_shares(base_config):
    """900 股 × sell_ratio=0.5 = 450 → 向下取整到 400，卖 400 保留 500"""
    market_data = [make_stock("000001.SZ", rise_pct=110.0, close_price=20.0)]
    holdings_map = {"000001.SZ": {"quantity": 900, "cost_price": 10.0}}
    signals = generate_signals(base_config, market_data, holdings_map)

    holdings = [make_holding("000001.SZ", qty=900, cost_price=10.0, close_price=20.0)]
    account = {"cash_balance": 50_000, "total_assets": 230_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "SELL"
    assert d.quantity == 400, f"900股 sell_ratio=0.5 应卖400股（450向下取整），实际卖了{d.quantity}股"
    assert d.quantity % 100 == 0, "卖出股数必须是100的整数倍"


def test_sell_ratio_100_shares(base_config):
    """100 股 × sell_ratio=0.5 = 50 → 取整为0 → 兜底全卖 100 股"""
    market_data = [make_stock("000001.SZ", rise_pct=110.0, close_price=20.0)]
    holdings_map = {"000001.SZ": {"quantity": 100, "cost_price": 10.0}}
    signals = generate_signals(base_config, market_data, holdings_map)

    holdings = [make_holding("000001.SZ", qty=100, cost_price=10.0, close_price=20.0)]
    account = {"cash_balance": 50_000, "total_assets": 52_000}
    decisions = make_decisions(base_config, signals, holdings, account)
    dm = decisions_map(decisions)

    d = dm["000001.SZ"]
    assert d.action == "SELL"
    assert d.quantity == 100, f"100股 sell_ratio=0.5 → 整手取整为0 → 兜底全卖，实际卖了{d.quantity}股"
