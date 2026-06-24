# -*- coding: utf-8 -*-
"""
新浪行情数据源（A 股）

接口: http://hq.sinajs.cn/list=sh600519,sz000001,...
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import requests

from .base import BaseFetcher, MARKET_A

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
BATCH_SIZE = 80
TIMEOUT = 5


class SinaFetcher(BaseFetcher):
    """新浪行情（A 股）"""

    name = 'sina'
    priority = 1
    supported_markets = frozenset({MARKET_A})

    def fetch(self, ts_codes: List[str], market: str = MARKET_A) -> Dict[str, dict]:
        results: Dict[str, dict] = {}
        if not ts_codes:
            return results

        sina_codes = [self._to_sina(c) for c in ts_codes]

        for i in range(0, len(sina_codes), BATCH_SIZE):
            batch = sina_codes[i:i + BATCH_SIZE]
            try:
                url = f"http://hq.sinajs.cn/list={','.join(batch)}"
                headers = {'Referer': 'http://finance.sina.com.cn'}
                resp = requests.get(url, headers=headers, timeout=TIMEOUT)
                resp.encoding = 'gbk'
                results.update(self._parse(resp.text))
            except requests.RequestException as e:
                logger.error(f"[{self.name}] request failed: {e}")

        return results

    @staticmethod
    def _to_sina(ts_code: str) -> str:
        code, exchange = ts_code.split('.')
        return ('sh' if exchange == 'SH' else 'sz') + code

    @staticmethod
    def _parse(text: str) -> Dict[str, dict]:
        results = {}
        for line in text.strip().split('\n'):
            if '=' not in line or '"' not in line:
                continue
            var_name = line.split('=')[0]
            sina_code = var_name.split('_')[-1]
            data_str = line.split('"')[1]
            if not data_str:
                continue
            parts = data_str.split(',')
            if len(parts) < 10:
                continue
            prefix = sina_code[:2]
            code = sina_code[2:]
            ts_code = f"{code}.{'SH' if prefix == 'sh' else 'SZ'}"
            try:
                price = float(parts[3])
                if price <= 0:
                    continue
                prev_close = float(parts[2])
                change_amt = price - prev_close
                change_pct = (change_amt / prev_close * 100) if prev_close > 0 else 0
                results[ts_code] = {
                    'name': parts[0],
                    'price': price,
                    'prev_close': prev_close,
                    'open': float(parts[1]),
                    'high': float(parts[4]),
                    'low': float(parts[5]),
                    'volume': float(parts[8]) / 100,   # 股 -> 手
                    'amount': float(parts[9]) / 10000,  # 元 -> 万元
                    'change_pct': round(change_pct, 2),
                    'change_amt': round(change_amt, 3),
                    'time': parts[31] if len(parts) > 31 else datetime.now(CST).strftime('%H:%M:%S'),
                }
            except (ValueError, IndexError):
                pass
        return results
