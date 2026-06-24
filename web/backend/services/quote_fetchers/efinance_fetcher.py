# -*- coding: utf-8 -*-
"""
[EXPERIMENTAL — NOT ENABLED IN PRODUCTION]

efinance 行情数据源（A 股）

PoC 验证结论（2026-03-25）：
  - efinance 0.5.8 的 get_realtime_quotes() 不支持按代码列表拉取
  - 它的参数是市场过滤条件（如"沪深京A股"），每次拉取整个市场全量
  - 与 quote_manager 的按 pending_codes 补齐模式不兼容
  - A股、港股测试均返回 "指定的行情参数不正确" 错误
  - 结论：暂不接入，efinance 不在 requirements.txt 中

如果未来重新评估（如 efinance 升级支持按代码拉取），可在此文件中更新实现，
并在 quote_manager.py 中重新注册：self._register(EfinanceFetcher())

当前状态：
  - 未安装 efinance 时，此文件安全加载（自动跳过注册）
  - 安装了 efinance 后，quote_manager 会尝试注册但 fetch() 会失败并返回空 {}
  - 不影响 tencent_a / sina 主链路

依赖: pip install efinance（不要加入 requirements.txt）
官方: https://github.com/Micro-sheep/efinance
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from .base import BaseFetcher, MARKET_A

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# 检查 efinance 是否可用
try:
    import efinance as _ef
    _EFINANCE_AVAILABLE = True
    logger.info("[efinance_fetcher] efinance 已加载")
except ImportError:
    _ef = None
    _EFINANCE_AVAILABLE = False
    logger.info("[efinance_fetcher] efinance 未安装，此数据源不可用")


class EfinanceFetcher(BaseFetcher):
    """
    efinance 行情（A 股）

    默认优先级 = 2（低于腾讯/新浪）。
    通过 poc_efinance.py 验证 PoC 后，如果稳定性更好，可将 priority 调整为 0。
    """

    name = 'efinance'
    priority = 2
    supported_markets = frozenset({MARKET_A})

    def __init__(self):
        if not _EFINANCE_AVAILABLE:
            logger.warning("[efinance_fetcher] efinance 未安装，EfinanceFetcher 不可用")

    @property
    def available(self) -> bool:
        return _EFINANCE_AVAILABLE

    def fetch(self, ts_codes: List[str], market: str = MARKET_A) -> Dict[str, dict]:
        if not _EFINANCE_AVAILABLE:
            return {}
        if not ts_codes:
            return {}

        # ts_code -> efinance 格式：去掉交易所后缀
        ef_codes = [c.split('.')[0] for c in ts_codes]
        # 建立反查映射: '600519' -> '600519.SH'
        code_map = {c.split('.')[0]: c for c in ts_codes}

        try:
            df = _ef.stock.get_realtime_quotes(ef_codes)
            if df is None or len(df) == 0:
                logger.warning("[efinance_fetcher] 返回空数据")
                return {}
            return self._normalize(df, code_map)
        except Exception as e:
            logger.error(f"[efinance_fetcher] fetch failed: {e}")
            return {}

    @staticmethod
    def _normalize(df, code_map: Dict[str, str]) -> Dict[str, dict]:
        """将 efinance DataFrame 归一化为标准 schema"""
        results = {}
        now_str = datetime.now(CST).strftime('%H:%M:%S')

        for _, row in df.iterrows():
            ef_code = str(row.get('股票代码', ''))
            if not ef_code:
                continue

            ts_code = code_map.get(ef_code)
            if not ts_code:
                # 根据代码前缀推断交易所
                exchange = 'SH' if ef_code.startswith('6') else 'SZ'
                ts_code = f"{ef_code}.{exchange}"

            try:
                price = float(row.get('最新价') or 0)
                if price <= 0:
                    continue

                prev_close = float(row.get('昨收') or 0)
                open_price = float(row.get('今开') or 0)
                high = float(row.get('最高') or 0)
                low = float(row.get('最低') or 0)

                # 成交量：efinance 返回"手"（已通过 PoC 确认，如不对请修改此处）
                volume = float(row.get('成交量') or 0)

                # 成交额：efinance 返回元，换算为万元
                amount_raw = float(row.get('成交额') or 0)
                amount = amount_raw / 10000

                change_pct = float(row.get('涨跌幅') or 0)
                change_amt = float(row.get('涨跌额') or 0)
                name = str(row.get('股票名称') or '')

                results[ts_code] = {
                    'name': name,
                    'price': price,
                    'prev_close': prev_close,
                    'open': open_price,
                    'high': high,
                    'low': low,
                    'volume': volume,
                    'amount': amount,
                    'change_pct': round(change_pct, 2),
                    'change_amt': round(change_amt, 3),
                    'time': now_str,
                }
            except (ValueError, TypeError):
                continue

        return results
