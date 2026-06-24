# -*- coding: utf-8 -*-
"""
回测引擎单元测试 — backtest.py 核心函数
==========================================
覆盖场景：
  1. 卖出按 sell_ratio 减仓（涨100%卖50%，不是全仓清空）
  2. 卖出股数是 100 的整数倍
  3. 减仓后剩余持仓保留在 holdings 中
  4. 持仓不足100股时全卖
  5. 买入条件同时检查 PB 和 PE
  6. market_pb_halt 暂停所有买入
  7. market_pb_halt 不影响卖出
  8. 行业锚点正确应用（不同行业有不同 pb_anchor）
  9. 行业仓位上限阻止买入
 10. 总仓位上限阻止买入
 11. 偏低入场加仓（PB ≤ 1.5，跌 ≥ 10% 触发）
 12. 偏高入场加仓（PB > 1.5，需跌 ≥ 20%）
 13. NAV 最大回撤计算
 14. 手续费计算（买入: commission only）
 15. 印花税计算（卖出: commission + stamp_tax）
 16. 配置解析默认值
 17. 月末 rebalance 日期提取
"""

import sys
import os

# 让测试能直接 import backtest 而不依赖安装
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'strategy'))

import pytest
from backtest import (
    BTHolding, BTState,
    _max_drawdown,
    _parse_config,
    _get_monthly_rebalance_dates,
    _compute_signals,
    _apply_decisions,
    COMMISSION_RATE, STAMP_TAX_RATE, MIN_COMMISSION,
)


# ─────────────────────────────────────────────────────────────
# 公共 fixtures / helpers
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def base_params():
    """回测引擎使用的扁平化参数（模拟 _parse_config 输出）"""
    return {
        "buy_pb_max": 2.0,
        "buy_pe_max": 20.0,
        "market_pb_halt": 2.5,
        "sell_rise_pct": 100.0,
        "sell_thresholds": [
            {"rise_pct": 100, "sell_ratio": 0.5},
            {"rise_pct": 200, "sell_ratio": 0.5},
        ],
        "anchors_by_industry": {},
        "default_pb_anchor": 1.0,
        "high_pb_threshold": 1.5,
        "high_pb_drop_pct": 20.0,
        "low_pb_drop_pct": 10.0,
        "max_industry_pct": 3.0,
        "max_total_pct": 66.7,
        "per_stock_pct": 3.0,
        "max_stocks": 60,
    }


def make_snapshot_row(ts_code, pb=0.8, pe_ttm=8.0, close_price=10.0, min_1y_low=5.0):
    """构造一条 snapshot 行数据"""
    return {
        "ts_code": ts_code,
        "pb": pb,
        "pe_ttm": pe_ttm,
        "close_price": close_price,
        "min_1y_low": min_1y_low,
    }


def make_industry_map(ts_code, industry="消费", name=None):
    """构造行业映射"""
    return {ts_code: {"industry": industry, "name": name or ts_code}}


# ─────────────────────────────────────────────────────────────
# 1. 卖出按 sell_ratio 减仓（不是全仓清空）
# ─────────────────────────────────────────────────────────────

def test_sell_by_ratio_not_full(base_params):
    """涨 100% → sell_ratio=0.5, 应卖 50% 而非全仓"""
    state = BTState(cash=50_000, holdings={
        "000001.SZ": BTHolding(ts_code="000001.SZ", name="平安", industry="金融",
                               quantity=1000, avg_cost=10.0, total_cost=10000.0),
    })
    # close=20, min_1y_low=10 → rise_pct=100%
    snapshot = [make_snapshot_row("000001.SZ", pb=0.8, pe_ttm=8.0, close_price=20.0, min_1y_low=10.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    prices = {"000001.SZ": 20.0}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    # 验证信号携带 sell_ratio
    sell_sigs = [s for s in signals if s["signal"] == "SELL_CANDIDATE"]
    assert len(sell_sigs) == 1
    assert sell_sigs[0]["sell_ratio"] == 0.5

    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    # 不应全仓清空 — 应只卖 500 股
    assert "000001.SZ" in state.holdings, "减仓后应保留剩余持仓"
    assert state.holdings["000001.SZ"].quantity == 500


# ─────────────────────────────────────────────────────────────
# 2. 卖出股数是 100 的整数倍
# ─────────────────────────────────────────────────────────────

def test_sell_qty_rounded_to_lots(base_params):
    """sell_ratio=0.5, qty=300 → target=150 → round to 100"""
    state = BTState(cash=50_000, holdings={
        "000001.SZ": BTHolding(ts_code="000001.SZ", name="平安", industry="金融",
                               quantity=300, avg_cost=10.0, total_cost=3000.0),
    })
    snapshot = [make_snapshot_row("000001.SZ", close_price=20.0, min_1y_low=10.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    prices = {"000001.SZ": 20.0}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    assert len(trade_records) == 1
    sell_qty = trade_records[0]["quantity"]
    assert sell_qty % 100 == 0, f"卖出股数 {sell_qty} 不是100的整数倍"


# ─────────────────────────────────────────────────────────────
# 3. 减仓后剩余持仓保留在 holdings 中
# ─────────────────────────────────────────────────────────────

def test_partial_sell_retains_remaining(base_params):
    """减仓后 holdings 中该股票的 quantity 正确减少"""
    initial_qty = 1000
    state = BTState(cash=50_000, holdings={
        "000001.SZ": BTHolding(ts_code="000001.SZ", name="平安", industry="金融",
                               quantity=initial_qty, avg_cost=10.0, total_cost=10000.0),
    })
    snapshot = [make_snapshot_row("000001.SZ", close_price=20.0, min_1y_low=10.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    prices = {"000001.SZ": 20.0}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    remaining = state.holdings["000001.SZ"].quantity
    sold = trade_records[0]["quantity"]
    assert remaining + sold == initial_qty, "剩余 + 卖出 = 原始数量"
    assert remaining > 0, "减仓后应有剩余"


# ─────────────────────────────────────────────────────────────
# 4. 持仓不足 100 股时全卖
# ─────────────────────────────────────────────────────────────

def test_sell_all_when_less_than_100_shares(base_params):
    """持仓只有 50 股, sell_ratio=0.5 → target=25 < 100 → 全卖"""
    state = BTState(cash=50_000, holdings={
        "000001.SZ": BTHolding(ts_code="000001.SZ", name="平安", industry="金融",
                               quantity=50, avg_cost=10.0, total_cost=500.0),
    })
    snapshot = [make_snapshot_row("000001.SZ", close_price=20.0, min_1y_low=10.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    prices = {"000001.SZ": 20.0}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    assert len(trade_records) == 1
    assert trade_records[0]["quantity"] == 50, "不足100股应全部卖出"
    assert "000001.SZ" not in state.holdings, "全卖后应从 holdings 中移除"


# ─────────────────────────────────────────────────────────────
# 5. 买入同时检查 PB 和 PE
# ─────────────────────────────────────────────────────────────

def test_buy_requires_both_pb_and_pe(base_params):
    """PB 合格但 PE 超限 → SKIP"""
    snapshot = [make_snapshot_row("000001.SZ", pb=0.8, pe_ttm=25.0, close_price=10.0, min_1y_low=8.0)]
    industry_map = make_industry_map("000001.SZ")
    signals = _compute_signals(snapshot, {}, industry_map, base_params)
    assert signals[0]["signal"] == "SKIP"
    assert "PE" in signals[0]["reason"]


def test_buy_requires_pb_ok(base_params):
    """PB 超限但 PE 合格 → SKIP"""
    snapshot = [make_snapshot_row("000001.SZ", pb=2.5, pe_ttm=8.0, close_price=10.0, min_1y_low=8.0)]
    industry_map = make_industry_map("000001.SZ")
    signals = _compute_signals(snapshot, {}, industry_map, base_params)
    assert signals[0]["signal"] == "SKIP"


def test_buy_candidate_both_ok(base_params):
    """PB 和 PE 都合格 + pb_ratio ≤ 1.0 → BUY_CANDIDATE"""
    snapshot = [make_snapshot_row("000001.SZ", pb=0.8, pe_ttm=8.0, close_price=10.0, min_1y_low=8.0)]
    industry_map = make_industry_map("000001.SZ")
    signals = _compute_signals(snapshot, {}, industry_map, base_params)
    assert signals[0]["signal"] == "BUY_CANDIDATE"


# ─────────────────────────────────────────────────────────────
# 6. market_pb_halt 暂停所有买入
# ─────────────────────────────────────────────────────────────

def test_market_pb_halt_skips_buy(base_params):
    """全市场 PB > 2.5 时非持仓股全部 SKIP"""
    snapshot = [
        make_snapshot_row("000001.SZ", pb=0.8, pe_ttm=8.0, close_price=10.0, min_1y_low=8.0),
        make_snapshot_row("000002.SZ", pb=0.9, pe_ttm=9.0, close_price=12.0, min_1y_low=10.0),
    ]
    industry_map = {
        "000001.SZ": {"industry": "消费", "name": "平安"},
        "000002.SZ": {"industry": "银行", "name": "万科"},
    }
    signals = _compute_signals(snapshot, {}, industry_map, base_params, market_pb=3.0)
    for s in signals:
        assert s["signal"] == "SKIP"
        assert "市场PB" in s["reason"]


# ─────────────────────────────────────────────────────────────
# 7. market_pb_halt 不影响卖出
# ─────────────────────────────────────────────────────────────

def test_market_pb_halt_does_not_block_sell(base_params):
    """市场 PB 暂停只阻止买入，持仓卖出信号不受影响"""
    state_holdings = {
        "000001.SZ": BTHolding(ts_code="000001.SZ", name="平安", industry="金融",
                               quantity=1000, avg_cost=10.0, total_cost=10000.0),
    }
    # rise_pct = (20-10)/10*100 = 100% → 达到卖出阈值
    snapshot = [make_snapshot_row("000001.SZ", close_price=20.0, min_1y_low=10.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    signals = _compute_signals(snapshot, state_holdings, industry_map, base_params, market_pb=3.0)
    assert signals[0]["signal"] == "SELL_CANDIDATE"


# ─────────────────────────────────────────────────────────────
# 8. 行业锚点正确应用
# ─────────────────────────────────────────────────────────────

def test_industry_anchor_differentiation(base_params):
    """不同行业使用不同 pb_anchor，影响 BUY_CANDIDATE 判断"""
    # 银行锚点=0.6, PB=0.8 → pb_ratio=1.33 > 1.0 → SKIP
    # 消费锚点=1.5, PB=0.8 → pb_ratio=0.53 ≤ 1.0 → BUY_CANDIDATE
    base_params["anchors_by_industry"] = {"银行": 0.6, "消费": 1.5}
    snapshot = [
        make_snapshot_row("600036.SH", pb=0.8, pe_ttm=8.0, close_price=30.0, min_1y_low=25.0),
        make_snapshot_row("000001.SZ", pb=0.8, pe_ttm=8.0, close_price=10.0, min_1y_low=8.0),
    ]
    industry_map = {
        "600036.SH": {"industry": "银行", "name": "招行"},
        "000001.SZ": {"industry": "消费", "name": "平安"},
    }
    signals = _compute_signals(snapshot, {}, industry_map, base_params)
    sig_map = {s["ts_code"]: s for s in signals}

    assert sig_map["600036.SH"]["signal"] == "SKIP", "银行 pb_ratio=1.33 不满足买入"
    assert sig_map["000001.SZ"]["signal"] == "BUY_CANDIDATE", "消费 pb_ratio=0.53 满足买入"
    assert sig_map["600036.SH"]["pb_anchor"] == 0.6
    assert sig_map["000001.SZ"]["pb_anchor"] == 1.5


# ─────────────────────────────────────────────────────────────
# 9. 行业仓位上限阻止买入
# ─────────────────────────────────────────────────────────────

def test_industry_cap_blocks_buy(base_params):
    """同行业已有持仓，加上新买入后超 max_industry_pct → 不买"""
    # 总资产 = cash + holdings_mv = 50000 + 1000*20 = 70000
    # per_stock_pct = 3% → target = 70000*0.03 = 2100
    # 同行业已有 20000 市值 → (20000+2100)/70000 = 31.6% >> 3%
    state = BTState(cash=50_000, holdings={
        "600036.SH": BTHolding(ts_code="600036.SH", name="招行", industry="金融",
                               quantity=1000, avg_cost=20.0, total_cost=20000.0),
    })
    snapshot = [make_snapshot_row("000001.SZ", pb=0.8, pe_ttm=8.0, close_price=10.0, min_1y_low=8.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    prices = {"600036.SH": 20.0, "000001.SZ": 10.0}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    assert signals[0]["signal"] == "BUY_CANDIDATE"

    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)
    # 不应有买入记录
    buy_records = [t for t in trade_records if t["action"] == "BUY"]
    assert len(buy_records) == 0, "行业仓位超限不应买入"


# ─────────────────────────────────────────────────────────────
# 10. 总仓位上限阻止买入
# ─────────────────────────────────────────────────────────────

def test_total_position_cap_blocks_buy(base_params):
    """总仓位 67% > max_total_pct=66.7% → 不买"""
    # cash=30000, holding_mv=70000 → total=100000
    # pos_mkt/total = 70% > 66.7% → break
    state = BTState(cash=30_000, holdings={
        "600036.SH": BTHolding(ts_code="600036.SH", name="招行", industry="银行",
                               quantity=1000, avg_cost=70.0, total_cost=70000.0),
    })
    snapshot = [make_snapshot_row("000001.SZ", pb=0.8, pe_ttm=8.0, close_price=10.0, min_1y_low=8.0)]
    industry_map = make_industry_map("000001.SZ", "消费", "平安")
    prices = {"600036.SH": 70.0, "000001.SZ": 10.0}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    buy_records = [t for t in trade_records if t["action"] == "BUY"]
    assert len(buy_records) == 0, "总仓位超限不应买入"


# ─────────────────────────────────────────────────────────────
# 11. 偏低入场加仓（PB ≤ 1.5，跌 ≥ 10% 触发）
# ─────────────────────────────────────────────────────────────

def test_add_position_low_pb_trigger(base_params):
    """PB=1.2 ≤ 1.5（偏低入场），当前价较均价跌 15% → 应加仓"""
    # 使用大现金使持仓占比远低于行业/总仓位上限
    # total_assets ≈ 5_000_000 + 42500 = 5_042_500
    # ind_mkt = 42500, target ≈ 5_042_500 * 3% ≈ 151275
    # (42500 + 151275) / 5_042_500 = 3.84% > 3% 仍会被挡
    # 所以提高 max_industry_pct 到 10% 来测试加仓逻辑本身
    params = {**base_params, "max_industry_pct": 10.0, "per_stock_pct": 3.0}
    state = BTState(cash=5_000_000, holdings={
        "600036.SH": BTHolding(ts_code="600036.SH", name="招行", industry="银行",
                               quantity=1000, avg_cost=50.0, total_cost=50000.0),
    })
    # close=42.5, avg_cost=50 → drop = (50-42.5)/50*100 = 15% >= 10%
    snapshot = [make_snapshot_row("600036.SH", pb=1.2, pe_ttm=8.0, close_price=42.5, min_1y_low=40.0)]
    industry_map = make_industry_map("600036.SH", "银行", "招行")
    prices = {"600036.SH": 42.5}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, params)
    assert signals[0]["signal"] == "HOLD"

    _apply_decisions(state, signals, "2025-01-31", params, trade_records, prices)
    buy_records = [t for t in trade_records if t["action"] == "BUY"]
    assert len(buy_records) == 1, "偏低入场跌15%应触发加仓"
    assert buy_records[0]["quantity"] % 100 == 0


def test_no_add_position_low_pb_insufficient_drop(base_params):
    """PB=1.2（偏低入场需10%），只跌5% → 不加仓"""
    state = BTState(cash=900_000, holdings={
        "600036.SH": BTHolding(ts_code="600036.SH", name="招行", industry="银行",
                               quantity=1000, avg_cost=50.0, total_cost=50000.0),
    })
    # close=47.5, drop = (50-47.5)/50*100 = 5% < 10%
    snapshot = [make_snapshot_row("600036.SH", pb=1.2, pe_ttm=8.0, close_price=47.5, min_1y_low=45.0)]
    industry_map = make_industry_map("600036.SH", "银行", "招行")
    prices = {"600036.SH": 47.5}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)
    assert len(trade_records) == 0, "跌幅不足不应加仓"


# ─────────────────────────────────────────────────────────────
# 12. 偏高入场加仓（PB > 1.5，需跌 ≥ 20%）
# ─────────────────────────────────────────────────────────────

def test_add_position_high_pb_needs_20pct_drop(base_params):
    """PB=1.8 > 1.5（偏高入场需20%），跌15%不够 → 不加仓"""
    state = BTState(cash=900_000, holdings={
        "601318.SH": BTHolding(ts_code="601318.SH", name="中国平安", industry="保险",
                               quantity=500, avg_cost=50.0, total_cost=25000.0),
    })
    # close=42.5 → drop = (50-42.5)/50 = 15% < 20%
    snapshot = [make_snapshot_row("601318.SH", pb=1.8, pe_ttm=12.0, close_price=42.5, min_1y_low=40.0)]
    industry_map = make_industry_map("601318.SH", "保险", "中国平安")
    prices = {"601318.SH": 42.5}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)
    assert len(trade_records) == 0, "偏高入场跌15%不够，不应加仓"


def test_add_position_high_pb_with_enough_drop(base_params):
    """PB=1.8，跌25% → 触发加仓"""
    # 提高 max_industry_pct 避免仓位上限挡住加仓测试
    params = {**base_params, "max_industry_pct": 10.0, "per_stock_pct": 3.0}
    state = BTState(cash=5_000_000, holdings={
        "601318.SH": BTHolding(ts_code="601318.SH", name="中国平安", industry="保险",
                               quantity=500, avg_cost=50.0, total_cost=25000.0),
    })
    # close=37.5 → drop = (50-37.5)/50 = 25% >= 20%
    snapshot = [make_snapshot_row("601318.SH", pb=1.8, pe_ttm=12.0, close_price=37.5, min_1y_low=35.0)]
    industry_map = make_industry_map("601318.SH", "保险", "中国平安")
    prices = {"601318.SH": 37.5}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, params)
    _apply_decisions(state, signals, "2025-01-31", params, trade_records, prices)
    buy_records = [t for t in trade_records if t["action"] == "BUY"]
    assert len(buy_records) == 1, "偏高入场跌25%应触发加仓"


# ─────────────────────────────────────────────────────────────
# 13. NAV 最大回撤计算
# ─────────────────────────────────────────────────────────────

def test_max_drawdown_normal():
    """正常回撤: 100→120→90 → dd = (120-90)/120 = 25%"""
    assert _max_drawdown([100, 120, 90]) == 25.0


def test_max_drawdown_monotonic_up():
    """单调上升 → 回撤=0"""
    assert _max_drawdown([100, 110, 120, 130]) == 0.0


def test_max_drawdown_empty_or_single():
    """空列表或单元素 → 回撤=0"""
    assert _max_drawdown([]) == 0.0
    assert _max_drawdown([100]) == 0.0


def test_max_drawdown_complex():
    """复杂路径: 100→150→120→180→90 → peak=180, dd=(180-90)/180=50%"""
    assert _max_drawdown([100, 150, 120, 180, 90]) == 50.0


def test_max_drawdown_precision():
    """精度: 100→100→95 → dd=5%"""
    assert _max_drawdown([100, 100, 95]) == 5.0


# ─────────────────────────────────────────────────────────────
# 14. 手续费计算（买入: commission only）
# ─────────────────────────────────────────────────────────────

def test_buy_commission_calculation(base_params):
    """买入手续费 = max(gross * 0.0003, 5.0), 现金减少 = gross + comm"""
    state = BTState(cash=1_000_000, holdings={})
    snapshot = [make_snapshot_row("000001.SZ", pb=0.8, pe_ttm=8.0, close_price=10.0, min_1y_low=8.0)]
    industry_map = make_industry_map("000001.SZ")
    prices = {"000001.SZ": 10.0}
    trade_records = []
    initial_cash = state.cash

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    assert len(trade_records) == 1
    rec = trade_records[0]
    assert rec["action"] == "BUY"
    assert rec["stamp_tax"] == 0, "买入不应有印花税"

    gross = rec["quantity"] * rec["price"]
    expected_comm = max(round(gross * COMMISSION_RATE, 2), MIN_COMMISSION)
    assert rec["commission"] == expected_comm

    # 验证现金扣减
    assert abs(state.cash - (initial_cash - gross - expected_comm)) < 0.01


# ─────────────────────────────────────────────────────────────
# 15. 印花税计算（卖出: commission + stamp_tax）
# ─────────────────────────────────────────────────────────────

def test_sell_fee_and_stamp_tax(base_params):
    """卖出: comm + stamp_tax, 现金增加 = gross - tax - comm"""
    state = BTState(cash=50_000, holdings={
        "000001.SZ": BTHolding(ts_code="000001.SZ", name="平安", industry="金融",
                               quantity=1000, avg_cost=10.0, total_cost=10000.0),
    })
    snapshot = [make_snapshot_row("000001.SZ", close_price=20.0, min_1y_low=10.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    prices = {"000001.SZ": 20.0}
    trade_records = []
    initial_cash = state.cash

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    assert len(trade_records) == 1
    rec = trade_records[0]
    assert rec["action"] == "SELL"

    gross = rec["quantity"] * rec["price"]
    expected_tax = round(gross * STAMP_TAX_RATE, 2)
    expected_comm = max(round(gross * COMMISSION_RATE, 2), MIN_COMMISSION)

    assert rec["stamp_tax"] == expected_tax
    assert rec["commission"] == expected_comm

    # 验证现金变动
    expected_cash = initial_cash + gross - expected_tax - expected_comm
    assert abs(state.cash - expected_cash) < 0.01


# ─────────────────────────────────────────────────────────────
# 16. 配置解析默认值
# ─────────────────────────────────────────────────────────────

def test_parse_config_defaults():
    """空配置 → 使用合理默认值"""
    params = _parse_config({})
    assert params["buy_pb_max"] == 2.0
    assert params["buy_pe_max"] == 20.0
    assert params["sell_rise_pct"] == 100.0
    assert params["max_industry_pct"] == 3.0
    assert params["max_total_pct"] == 66.7
    assert params["high_pb_threshold"] == 1.5
    assert params["high_pb_drop_pct"] == 20.0
    assert params["low_pb_drop_pct"] == 10.0
    assert isinstance(params["anchors_by_industry"], dict)


def test_parse_config_with_values():
    """自定义配置 → 正确解析"""
    config = {
        "buy_rules": {
            "entry": {"max_pb": 3.0, "max_pe": 30.0, "market_pb_halt": 3.0},
            "batch": {"high_pb_threshold": 2.0, "high_pb_drop_pct": 25.0, "low_pb_drop_pct": 15.0},
            "position_limits": {"max_industry_pct": 5.0, "max_total_position_pct": 80.0},
        },
        "sell_rules": {
            "method": "from_1y_low",
            "from_1y_low": {"thresholds": [{"rise_pct": 50, "sell_ratio": 0.3}]},
        },
    }
    params = _parse_config(config)
    assert params["buy_pb_max"] == 3.0
    assert params["buy_pe_max"] == 30.0
    assert params["market_pb_halt"] == 3.0
    assert params["sell_rise_pct"] == 50.0
    assert params["max_industry_pct"] == 5.0
    assert params["max_total_pct"] == 80.0
    assert params["high_pb_threshold"] == 2.0
    assert len(params["sell_thresholds"]) == 1


# ─────────────────────────────────────────────────────────────
# 17. 月末 rebalance 日期提取
# ─────────────────────────────────────────────────────────────

def test_monthly_rebalance_dates():
    """每月取最后一个交易日"""
    trading_dates = [
        "2024-01-29", "2024-01-30", "2024-01-31",
        "2024-02-27", "2024-02-28", "2024-02-29",
        "2024-03-28", "2024-03-29",
    ]
    result = _get_monthly_rebalance_dates(trading_dates)
    assert result == ["2024-01-31", "2024-02-29", "2024-03-29"]


def test_monthly_rebalance_dates_single_month():
    """单月只有一天"""
    result = _get_monthly_rebalance_dates(["2024-01-15"])
    assert result == ["2024-01-15"]


def test_monthly_rebalance_dates_empty():
    """空列表"""
    assert _get_monthly_rebalance_dates([]) == []


# ─────────────────────────────────────────────────────────────
# 18. sell_ratio=0.5 精度：1000 / 900 / 100 股三种持仓
# ─────────────────────────────────────────────────────────────

def test_backtest_sell_ratio_1000_shares(base_params):
    """1000 股 × sell_ratio=0.5 → 卖 500，保留 500（不是全仓清空）"""
    state = BTState(cash=50_000, holdings={
        "000001.SZ": BTHolding(ts_code="000001.SZ", name="平安", industry="金融",
                               quantity=1000, avg_cost=10.0, total_cost=10000.0),
    })
    # rise_pct = (20-10)/10*100 = 100% → 达到卖出阈值
    snapshot = [make_snapshot_row("000001.SZ", close_price=20.0, min_1y_low=10.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    prices = {"000001.SZ": 20.0}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    assert len(trade_records) == 1
    assert trade_records[0]["quantity"] == 500, \
        f"1000股 sell_ratio=0.5 应卖500股，实际卖了{trade_records[0]['quantity']}股"
    assert state.holdings["000001.SZ"].quantity == 500, "卖500后应保留500股"


def test_backtest_sell_ratio_900_shares(base_params):
    """900 股 × sell_ratio=0.5 = 450 → 向下取整到 400，卖 400 保留 500"""
    state = BTState(cash=50_000, holdings={
        "000001.SZ": BTHolding(ts_code="000001.SZ", name="平安", industry="金融",
                               quantity=900, avg_cost=10.0, total_cost=9000.0),
    })
    snapshot = [make_snapshot_row("000001.SZ", close_price=20.0, min_1y_low=10.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    prices = {"000001.SZ": 20.0}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    assert len(trade_records) == 1
    sell_qty = trade_records[0]["quantity"]
    assert sell_qty == 400, \
        f"900股 sell_ratio=0.5 应卖400股（450向下取整），实际卖了{sell_qty}股"
    assert sell_qty % 100 == 0, "卖出股数必须是100的整数倍"
    assert state.holdings["000001.SZ"].quantity == 500


def test_backtest_sell_ratio_100_shares(base_params):
    """100 股 × sell_ratio=0.5 = 50 → 取整为0 → 兜底全卖 100 股"""
    state = BTState(cash=50_000, holdings={
        "000001.SZ": BTHolding(ts_code="000001.SZ", name="平安", industry="金融",
                               quantity=100, avg_cost=10.0, total_cost=1000.0),
    })
    snapshot = [make_snapshot_row("000001.SZ", close_price=20.0, min_1y_low=10.0)]
    industry_map = make_industry_map("000001.SZ", "金融", "平安")
    prices = {"000001.SZ": 20.0}
    trade_records = []

    signals = _compute_signals(snapshot, state.holdings, industry_map, base_params)
    _apply_decisions(state, signals, "2025-01-31", base_params, trade_records, prices)

    assert len(trade_records) == 1
    assert trade_records[0]["quantity"] == 100, \
        f"100股 sell_ratio=0.5 → 整手取整为0 → 兜底全卖，实际卖了{trade_records[0]['quantity']}股"
    assert "000001.SZ" not in state.holdings, "全卖后应从 holdings 中移除"
