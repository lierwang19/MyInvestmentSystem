# -*- coding: utf-8 -*-
"""
腾讯行情数据源（A 股 + 港股）

A 股接口:  http://qt.gtimg.cn/q=sh600519,sz000001,...
港股接口:  http://qt.gtimg.cn/q=hk00700,hk09988,...
"""

import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import requests

from .base import BaseFetcher, MARKET_A, MARKET_HK

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
BATCH_SIZE = 80
TIMEOUT = 5


class TencentFetcher(BaseFetcher):
    """腾讯行情（A 股）"""

    name = 'tencent_a'
    priority = 0
    supported_markets = frozenset({MARKET_A})

    def fetch(self, ts_codes: List[str], market: str = MARKET_A) -> Dict[str, dict]:
        results: Dict[str, dict] = {}
        if not ts_codes:
            return results

        sina_codes = [self._to_sina(c) for c in ts_codes]

        for i in range(0, len(sina_codes), BATCH_SIZE):
            batch = sina_codes[i:i + BATCH_SIZE]
            try:
                url = f"http://qt.gtimg.cn/q={','.join(batch)}"
                resp = requests.get(url, timeout=TIMEOUT)
                resp.encoding = 'gbk'
                results.update(self._parse(resp.text))
            except requests.RequestException as e:
                logger.error(f"[{self.name}] request failed: {e}")

        return results

    @staticmethod
    def _to_sina(ts_code: str) -> str:
        """600519.SH -> sh600519"""
        code, exchange = ts_code.split('.')
        return ('sh' if exchange == 'SH' else 'sz') + code

    @staticmethod
    def _parse(text: str) -> Dict[str, dict]:
        results = {}
        for line in text.strip().split(';'):
            line = line.strip()
            if '~' not in line:
                continue
            parts = line.split('~')
            if len(parts) < 35:
                continue
            var_match = re.search(r'v_(\w\w)', line)
            if not var_match:
                continue
            prefix = var_match.group(1)
            exchange = 'SH' if prefix == 'sh' else 'SZ'
            ts_code = f"{parts[2]}.{exchange}"
            try:
                price = float(parts[3]) if parts[3] else 0
                if price <= 0:
                    continue
                results[ts_code] = {
                    'name': parts[1],
                    'price': price,
                    'prev_close': float(parts[4]) if parts[4] else 0,
                    'open': float(parts[5]) if parts[5] else 0,
                    'high': float(parts[33]) if parts[33] else 0,
                    'low': float(parts[34]) if parts[34] else 0,
                    'volume': float(parts[6]) if parts[6] else 0,
                    'amount': float(parts[37]) if len(parts) > 37 and parts[37] else 0,
                    'change_pct': float(parts[32]) if parts[32] else 0,
                    'change_amt': float(parts[31]) if parts[31] else 0,
                    'time': datetime.now(CST).strftime('%H:%M:%S'),
                }
            except (ValueError, IndexError):
                pass
        return results


class TencentHKFetcher(BaseFetcher):
    """腾讯行情（港股）"""

    name = 'tencent_hk'
    priority = 0
    supported_markets = frozenset({MARKET_HK})

    def fetch(self, ts_codes: List[str], market: str = MARKET_HK) -> Dict[str, dict]:
        results: Dict[str, dict] = {}
        if not ts_codes:
            return results

        tencent_codes = ['hk' + c.split('.')[0] for c in ts_codes]

        for i in range(0, len(tencent_codes), BATCH_SIZE):
            batch = tencent_codes[i:i + BATCH_SIZE]
            try:
                url = f"http://qt.gtimg.cn/q={','.join(batch)}"
                resp = requests.get(url, timeout=TIMEOUT)
                resp.encoding = 'gbk'
                results.update(self._parse(resp.text))
            except requests.RequestException as e:
                logger.error(f"[{self.name}] request failed: {e}")

        return results

    @staticmethod
    def _parse(text: str) -> Dict[str, dict]:
        results = {}
        for line in text.strip().split(';'):
            line = line.strip()
            if '~' not in line:
                continue
            parts = line.split('~')
            if len(parts) < 35:
                continue
            var_match = re.search(r'v_(hk\d+)', line)
            if not var_match:
                continue
            tencent_code = var_match.group(1)
            ts_code = tencent_code[2:] + '.HK'
            try:
                price = float(parts[3]) if parts[3] else 0
                if price <= 0:
                    continue
                results[ts_code] = {
                    'name': parts[1],
                    'price': price,
                    'prev_close': float(parts[4]) if parts[4] else 0,
                    'open': float(parts[5]) if parts[5] else 0,
                    'high': float(parts[33]) if parts[33] else 0,
                    'low': float(parts[34]) if parts[34] else 0,
                    'change_pct': float(parts[32]) if parts[32] else 0,
                    'change_amt': float(parts[31]) if parts[31] else 0,
                    'time': datetime.now(CST).strftime('%H:%M:%S'),
                }
            except (ValueError, IndexError):
                pass
        return results
