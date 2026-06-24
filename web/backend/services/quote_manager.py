# -*- coding: utf-8 -*-
"""
QuoteManager — 实时行情数据源调度器

职责：
  - 维护多个数据源的优先级、失败计数、冷却时间
  - 按优先级依次尝试，任一成功则返回结果
  - 自动冷却：某数据源连续失败 N 次后进入冷却期，冷却期内跳过
  - 打日志：记录每次命中源、耗时、失败原因、切换次数

使用方式：
    from web.backend.services.quote_manager import quote_manager

    # 拉取 A 股行情
    quotes = quote_manager.fetch_a(["600519.SH", "000001.SZ"])

    # 拉取港股行情
    quotes = quote_manager.fetch_hk(["00700.HK"])

    # 拉取混合（自动按市场路由）
    quotes = quote_manager.fetch(["600519.SH", "00700.HK"])
"""

import logging
import time
from typing import Dict, List, Optional

from .quote_fetchers.base import BaseFetcher, MARKET_A, MARKET_HK
from .quote_fetchers.tencent_fetcher import TencentFetcher, TencentHKFetcher
from .quote_fetchers.sina_fetcher import SinaFetcher
from .quote_fetchers.efinance_fetcher import EfinanceFetcher  # experimental, not registered

logger = logging.getLogger(__name__)

# 熔断器参数
DEFAULT_FAIL_THRESHOLD = 3    # 连续失败 N 次后进入冷却
DEFAULT_COOLDOWN_SECS = 120   # 冷却期时长（秒）


class _FetcherState:
    """单个数据源的运行时状态（失败计数、冷却时间）"""

    def __init__(self, fetcher: BaseFetcher,
                 fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
                 cooldown_secs: int = DEFAULT_COOLDOWN_SECS):
        self.fetcher = fetcher
        self.fail_threshold = fail_threshold
        self.cooldown_secs = cooldown_secs
        self.consecutive_failures = 0
        self.cooldown_until: float = 0.0   # epoch seconds

    @property
    def is_cooling(self) -> bool:
        if self.cooldown_until and time.time() < self.cooldown_until:
            return True
        if self.cooldown_until and time.time() >= self.cooldown_until:
            # 冷却期结束，重置
            self.cooldown_until = 0.0
            self.consecutive_failures = 0
        return False

    def on_success(self):
        self.consecutive_failures = 0
        self.cooldown_until = 0.0

    def on_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.fail_threshold:
            self.cooldown_until = time.time() + self.cooldown_secs
            logger.warning(
                f"[quote_manager] {self.fetcher.name} 连续失败 "
                f"{self.consecutive_failures} 次，冷却 {self.cooldown_secs}s"
            )


class QuoteManager:
    """
    行情数据源调度器（单例模式，模块级 quote_manager 实例）

    初始化时自动注册内置数据源：
      A 股:  tencent_a (P0) → sina (P1) → efinance (P2, 仅在已安装时)
      港股:  tencent_hk (P0)
    """

    def __init__(self):
        self._states: Dict[str, _FetcherState] = {}

        # 注册 A 股数据源（按优先级顺序）
        self._register(TencentFetcher())
        self._register(SinaFetcher())
        # EfinanceFetcher 暂不接入生产：
        # PoC 验证结论（2026-03-25）API 不支持按代码列表拉取，与 pending 补齐模式不兼容。
        # 如需重新评估，请在 efinance_fetcher.py 中确认兼容性后手动取消下方注释。
        # self._register(EfinanceFetcher())

        # 注册港股数据源
        self._register(TencentHKFetcher())

        # 按市场分组，优先级排序
        self._fetchers_by_market: Dict[str, List[_FetcherState]] = {
            MARKET_A: sorted(
                [s for s in self._states.values() if s.fetcher.can_handle(MARKET_A)],
                key=lambda s: s.fetcher.priority
            ),
            MARKET_HK: sorted(
                [s for s in self._states.values() if s.fetcher.can_handle(MARKET_HK)],
                key=lambda s: s.fetcher.priority
            ),
        }

        a_names = [s.fetcher.name for s in self._fetchers_by_market[MARKET_A]]
        hk_names = [s.fetcher.name for s in self._fetchers_by_market[MARKET_HK]]
        logger.info(f"[quote_manager] 初始化完成  A股: {a_names}  港股: {hk_names}")

    def _register(self, fetcher: BaseFetcher):
        self._states[fetcher.name] = _FetcherState(fetcher)

    def fetch_a(self, ts_codes: List[str]) -> Dict[str, dict]:
        """拉取 A 股行情，自动 fallback"""
        return self._fetch_with_fallback(ts_codes, MARKET_A)

    def fetch_hk(self, ts_codes: List[str]) -> Dict[str, dict]:
        """拉取港股行情，自动 fallback"""
        return self._fetch_with_fallback(ts_codes, MARKET_HK)

    def fetch(self, ts_codes: List[str]) -> Dict[str, dict]:
        """
        拉取混合行情，按市场自动路由。
        ts_codes 中 .HK 结尾的走港股链路，其余走 A 股链路。
        """
        a_codes = [c for c in ts_codes if not c.endswith('.HK')]
        hk_codes = [c for c in ts_codes if c.endswith('.HK')]

        results = {}
        if a_codes:
            results.update(self.fetch_a(a_codes))
        if hk_codes:
            results.update(self.fetch_hk(hk_codes))
        return results

    def _fetch_with_fallback(self, ts_codes: List[str], market: str) -> Dict[str, dict]:
        """
        核心调度逻辑：按优先级逐源补齐，直到所有代码均命中或无源可用。

        每个 fetcher 返回后：
        - 从 pending_codes 扣掉已命中的代码
        - 只要命中 ≥1 只，视为该源可用（on_success），但仍继续补齐剩余代码
        - 返回空才视为失败（on_failure）并触发熔断计数
        - 最终返回所有源聚合后的结果
        """
        states = self._fetchers_by_market.get(market, [])
        if not states:
            logger.error(f"[quote_manager] 无可用数据源: market={market}")
            return {}

        aggregated: Dict[str, dict] = {}
        pending = list(ts_codes)   # 还未命中的代码列表
        tried = 0

        for state in states:
            if not pending:
                break

            if state.is_cooling:
                logger.debug(f"[quote_manager] {state.fetcher.name} 冷却中，跳过")
                continue

            tried += 1
            t0 = time.time()
            try:
                result = state.fetcher.fetch(pending, market)
                elapsed = time.time() - t0

                if result:
                    state.on_success()
                    # 从 pending 中移除已命中的代码
                    hit = [c for c in pending if c in result]
                    still_pending = [c for c in pending if c not in result]
                    aggregated.update(result)
                    pending = still_pending

                    if still_pending:
                        logger.debug(
                            f"[quote_manager] {state.fetcher.name} "
                            f"补齐 {len(hit)} 只，剩余 {len(still_pending)} 只待补  "
                            f"耗时 {elapsed:.2f}s  market={market}"
                        )
                    else:
                        logger.debug(
                            f"[quote_manager] ✅ {state.fetcher.name} "
                            f"全部命中 {len(hit)}/{len(ts_codes)} 只  "
                            f"耗时 {elapsed:.2f}s  market={market}"
                        )
                else:
                    # 判断是否是"残余脏代码"场景：
                    # 条件：本轮 aggregated 已有命中（说明主源正常工作）
                    #       且当前 pending ≤ 2（极少量剩余，可能是无效/退市代码）
                    # → 跳过熔断计数，避免正常备用源被脏数据打入冷却
                    # 反例：本次请求从头到尾就只有 1-2 只且全部未命中
                    #       → aggregated 为空，仍正常计入失败
                    is_residual = len(aggregated) > 0 and len(pending) <= 2
                    if is_residual:
                        logger.warning(
                            f"[quote_manager] {state.fetcher.name} 对残余 {len(pending)} 只代码返回空 "
                            f"（主源已命中 {len(aggregated)} 只，跳过熔断计数，可能是无效/退市代码）: {pending}  "
                            f"耗时 {elapsed:.2f}s"
                        )
                    else:
                        state.on_failure()
                        logger.warning(
                            f"[quote_manager] {state.fetcher.name} 返回空数据 "
                            f"(failures={state.consecutive_failures}) "
                            f"耗时 {elapsed:.2f}s  → 尝试下一源"
                        )

            except Exception as e:
                elapsed = time.time() - t0
                state.on_failure()
                logger.error(
                    f"[quote_manager] ❌ {state.fetcher.name} 异常: {e} "
                    f"(failures={state.consecutive_failures}) "
                    f"耗时 {elapsed:.2f}s  → 尝试下一源"
                )

        if tried == 0:
            logger.warning(f"[quote_manager] 所有 {market} 数据源均在冷却期，返回空")
        elif pending:
            logger.warning(
                f"[quote_manager] 所有源已尝试，仍有 {len(pending)} 只未命中: {pending[:5]}{'...' if len(pending)>5 else ''}  "
                f"market={market}"
            )

        return aggregated

    def get_status(self) -> dict:
        """返回各数据源当前状态（用于监控/调试）"""
        status = {}
        for name, state in self._states.items():
            status[name] = {
                'priority': state.fetcher.priority,
                'markets': list(state.fetcher.supported_markets),
                'consecutive_failures': state.consecutive_failures,
                'is_cooling': state.is_cooling,
                'cooldown_until': state.cooldown_until or None,
            }
        return status


# ── 模块级单例，quotes.py 直接导入使用
quote_manager = QuoteManager()
