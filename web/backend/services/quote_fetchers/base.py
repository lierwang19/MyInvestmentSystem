# -*- coding: utf-8 -*-
"""
BaseFetcher — 实时行情数据源统一接口

所有具体数据源（腾讯、新浪、efinance 等）继承此类并实现 fetch() 方法。

标准输出 schema（每只股票）：
    {
        'name': str,
        'price': float,
        'prev_close': float,
        'open': float,
        'high': float,
        'low': float,
        'volume': float,    # 手（100 股）
        'amount': float,    # 万元
        'change_pct': float, # 百分比，如 1.23 表示 +1.23%
        'change_amt': float,
        'time': str,        # 'HH:MM:SS'
    }
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, FrozenSet, List

logger = logging.getLogger(__name__)

# 所有支持的市场标识
MARKET_A = 'A'    # A 股（沪深）
MARKET_HK = 'HK'  # 港股


class BaseFetcher(ABC):
    """行情数据源抽象基类"""

    #: 数据源名称（用于日志和监控）
    name: str = 'base'

    #: 优先级（数值越小越优先）
    priority: int = 99

    #: 该数据源支持的市场
    supported_markets: FrozenSet[str] = frozenset({MARKET_A})

    def can_handle(self, market: str) -> bool:
        return market in self.supported_markets

    @abstractmethod
    def fetch(self, ts_codes: List[str], market: str = MARKET_A) -> Dict[str, dict]:
        """
        拉取行情。

        Args:
            ts_codes: 股票代码列表，格式如 ['600519.SH', '000001.SZ', '00700.HK']
            market:   市场标识，MARKET_A 或 MARKET_HK

        Returns:
            dict: ts_code -> 标准 schema dict。拉取失败时返回空 dict，不抛异常。
        """
        raise NotImplementedError
