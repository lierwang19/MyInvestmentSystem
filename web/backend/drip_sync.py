# -*- coding: utf-8 -*-
"""
drip_sync.py — 滴灌式持续数据同步（Drip Sync Worker）
每 8 分钟自动处理一个约 40 只股票的切片，优先级：持仓 → 优选池 → 最近失败 → 全市场轮巡
目标日期从 market.db MAX(trade_date) 动态获取，不用 date.today()
"""
from __future__ import annotations
import csv
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger("drip_sync")

from web.backend.config import PROJECT_ROOT
from web.backend.time_utils import sh_compact_timestamp, sh_date_str, sh_now, sh_timestamp_str

# ─── 全局写锁（drip / scheduler / 手动 ETL 三者互斥） ───────────────────────
market_db_write_lock = threading.Lock()

# ─── Drip 运行状态（进程内存，重启后重置） ───────────────────────────────────
DRIP_STATE: dict = {
    "status": "idle",        # idle | running | paused
    "enabled": True,
    "last_run": None,
    "last_batch_count": 0,
    "configured_batch_size": 40,
    "effective_batch_size": 40,
    "consecutive_success_slices": 0,
    "current_cycle": 0,
    "total_stocks": 0,
    "synced_today": 0,
    "last_error": None,
    "auto_paused_by": None,  # None | "manual_etl"
}

# 今日首轮标记
_today_first_run_date: Optional[str] = None
# 目标日期缓存
_target_date_cache: dict = {"price": None, "updated_at": None}
# 关闭请求
_shutdown_requested = False


# ─── 初始化 ─────────────────────────────────────────────────────────────────

def init_drip_sync():
    """建表（冗余保障）、reconcile_universe、refresh_priorities，系统启动时调用"""
    _load_enabled_state()
    reconcile_universe()
    refresh_priorities()
    logger.info("drip_sync: initialized")


def _load_enabled_state():
    """从 system_settings 读取 drip_sync_enabled"""
    try:
        from web.backend.api.settings import get_setting_value
        val = get_setting_value("drip_sync_enabled", "1")
        DRIP_STATE["enabled"] = (val == "1")
    except Exception:
        pass


# ─── Universe 对齐 ──────────────────────────────────────────────────────────

def reconcile_universe():
    """
    每次启动 + 每天首轮：将 drip_sync_progress 与 stock_industry_base.csv 对齐
    - 补入新股票
    - 恢复重新出现的股票（priority=999 → 99）
    - 标记退市股票（priority → 999）
    """
    base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    if not os.path.exists(base_path):
        logger.warning("drip: stock_industry_base.csv not found, skipping reconcile")
        return

    current_ts: set[str] = set()
    try:
        with open(base_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("ts_code") or "").strip()
                if code and not code.endswith(".BJ"):
                    current_ts.add(code)
    except Exception as e:
        logger.error(f"drip: reconcile_universe read error: {e}")
        return

    if not current_ts:
        return

    to_mark: set[str] = set()
    from web.backend.database import get_db
    try:
        with get_db() as conn:
            # 1. 补入新股票
            conn.executemany(
                "INSERT OR IGNORE INTO drip_sync_progress (ts_code, priority, cycle_seq) VALUES (?, 99, 0)",
                [(ts,) for ts in current_ts],
            )
            # 2. 恢复重新出现的股票 (priority=999 → 99)
            conn.executemany(
                "UPDATE drip_sync_progress SET priority = 99 WHERE ts_code = ? AND priority = 999",
                [(ts,) for ts in current_ts],
            )
            # 3. 标记退市股票
            all_db = {
                r[0] for r in conn.execute(
                    "SELECT ts_code FROM drip_sync_progress WHERE priority != 999"
                ).fetchall()
            }
            to_mark = all_db - current_ts
            if to_mark:
                conn.executemany(
                    "UPDATE drip_sync_progress SET priority = 999 WHERE ts_code = ?",
                    [(ts,) for ts in to_mark],
                )
            conn.commit()

        DRIP_STATE["total_stocks"] = len(current_ts)
        logger.info(
            f"drip: reconcile done, universe={len(current_ts)}, "
            f"marked_delisted={len(to_mark)}"
        )
    except Exception as e:
        logger.error(f"drip: reconcile_universe DB error: {e}")


def refresh_priorities():
    """从 holdings + stock_pool 刷新 priority 字段"""
    from web.backend.database import get_db
    holdings = []
    pool = []
    try:
        with get_db() as conn:
            # 重置 1/2 → 99（保留 999 退市标记）
            conn.execute(
                "UPDATE drip_sync_progress SET priority = 99 WHERE priority IN (1, 2)"
            )
            # 持仓 → priority 1
            holdings = conn.execute(
                "SELECT DISTINCT ts_code FROM holdings WHERE quantity > 0"
            ).fetchall()
            conn.executemany(
                "UPDATE drip_sync_progress SET priority = 1 WHERE ts_code = ? AND priority != 999",
                [(r[0],) for r in holdings],
            )
            # 优选池 → priority 2（非持仓）
            pool = conn.execute("SELECT DISTINCT ts_code FROM stock_pool").fetchall()
            conn.executemany(
                "UPDATE drip_sync_progress SET priority = 2 WHERE ts_code = ? AND priority NOT IN (1, 999)",
                [(r[0],) for r in pool],
            )
            conn.commit()
        logger.info(
            f"drip: refresh_priorities done, holdings={len(holdings)}, pool={len(pool)}"
        )
    except Exception as e:
        logger.error(f"drip: refresh_priorities error: {e}")


# ─── 目标日期 ────────────────────────────────────────────────────────────────

def get_target_dates() -> tuple[Optional[str], Optional[str]]:
    """
    从 market.db 查 MAX(trade_date) 作为目标日期，缓存当小时内的结果。
    估值目标日期对齐行情目标日期（不取 daily_valuation 自身避免自我滞后）。
    """
    now_key = sh_now().strftime("%Y-%m-%d %H")
    if _target_date_cache["updated_at"] == now_key and _target_date_cache["price"]:
        return _target_date_cache["price"], _target_date_cache["price"]

    from web.backend.database import get_market_db
    target_price: Optional[str] = None
    try:
        with get_market_db() as conn:
            if conn:
                row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
                target_price = row[0] if row else None
    except Exception as e:
        logger.warning(f"drip: get_target_dates error: {e}")

    _target_date_cache["price"] = target_price
    _target_date_cache["updated_at"] = now_key
    return target_price, target_price


# ─── 批次选取 ────────────────────────────────────────────────────────────────

def pick_next_batch(batch_size: int = 40) -> list[str]:
    """
    分页扫描 + market.db 二次校正：
    - 真实有缺口的股票加入结果
    - 伪缺失（已最新）的股票 bump cycle_seq 移到队尾
    """
    target_price, target_val = get_target_dates()
    if target_price is None:
        return _pick_never_synced(batch_size)

    result: list[str] = []
    offset = 0
    page_size = batch_size * 3

    while len(result) < batch_size:
        candidates = _query_candidates(offset, page_size)
        if not candidates:
            break

        real_gaps, already_current = _check_real_gaps(candidates, target_price, target_val)
        take = real_gaps[:batch_size - len(result)]
        result.extend(take)

        if already_current:
            _bump_cycle_seq(list(already_current))

        offset += page_size

    return result[:batch_size]


def _pick_never_synced(batch_size: int) -> list[str]:
    """market.db 为空时，选从未同步的股票"""
    from web.backend.database import get_db
    try:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT ts_code FROM drip_sync_progress
                   WHERE last_sync_at IS NULL AND priority < 999
                   ORDER BY priority ASC, cycle_seq ASC
                   LIMIT ?""",
                (batch_size,),
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _get_configured_batch_size(default_batch_size: int = 40) -> int:
    try:
        from web.backend.api.settings import get_setting_value
        batch_size = int(get_setting_value("drip_sync_batch_size", str(default_batch_size)))
        if batch_size < 1:
            batch_size = default_batch_size
    except Exception:
        batch_size = default_batch_size
    DRIP_STATE["configured_batch_size"] = batch_size
    return batch_size


def _get_effective_batch_size(default_batch_size: int = 40) -> int:
    configured = _get_configured_batch_size(default_batch_size)
    current = int(DRIP_STATE.get("effective_batch_size") or configured or default_batch_size)
    effective = min(configured, max(1, current))
    DRIP_STATE["effective_batch_size"] = effective
    return effective


def _record_slice_feedback(*, had_timeout: bool, configured_batch_size: int) -> None:
    """
    自适应批大小：
    - 发生 timeout：批大小减半，至少保留 5
    - 连续 3 轮无 timeout：批大小 +5，逐步回升到配置值
    """
    current = int(DRIP_STATE.get("effective_batch_size") or configured_batch_size or 40)
    configured = max(1, configured_batch_size)
    if had_timeout:
        DRIP_STATE["consecutive_success_slices"] = 0
        DRIP_STATE["effective_batch_size"] = max(5, min(configured, current // 2 if current > 1 else 1))
        return

    success_runs = int(DRIP_STATE.get("consecutive_success_slices") or 0) + 1
    DRIP_STATE["consecutive_success_slices"] = success_runs
    if success_runs >= 3 and current < configured:
        DRIP_STATE["effective_batch_size"] = min(configured, current + 5)
        DRIP_STATE["consecutive_success_slices"] = 0


def _query_candidates(offset: int, page_size: int) -> list[str]:
    """按优先级分页取候选"""
    from web.backend.database import get_db
    try:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT ts_code FROM drip_sync_progress
                   WHERE fail_count < 5 AND priority < 999
                   ORDER BY
                     CASE WHEN priority = 1 THEN 0
                          WHEN priority = 2 THEN 1
                          WHEN fail_count BETWEEN 1 AND 4 THEN 2
                          ELSE 3 END,
                     cycle_seq ASC
                   LIMIT ? OFFSET ?""",
                (page_size, offset),
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _check_real_gaps(
    candidates: list[str], target_price: str, target_val: str
) -> tuple[list[str], set[str]]:
    """
    market.db 二次校正：区分真实缺口和伪缺失
    返回 (has_gap_list, already_current_set)
    """
    from web.backend.database import get_market_db
    has_gap: list[str] = []
    already_current: set[str] = set()

    if not candidates:
        return has_gap, already_current

    placeholders = ",".join(["?" for _ in candidates])

    try:
        with get_market_db() as conn:
            if conn is None:
                return list(candidates), already_current

            price_rows = conn.execute(
                f"SELECT ts_code, MAX(trade_date) FROM daily_price "
                f"WHERE ts_code IN ({placeholders}) GROUP BY ts_code",
                candidates,
            ).fetchall()
            price_status = {r[0]: r[1] for r in price_rows}

            val_rows = conn.execute(
                f"SELECT ts_code, MAX(trade_date) FROM daily_valuation "
                f"WHERE ts_code IN ({placeholders}) GROUP BY ts_code",
                candidates,
            ).fetchall()
            val_status = {r[0]: r[1] for r in val_rows}

        for ts in candidates:
            p_date = price_status.get(ts)
            v_date = val_status.get(ts)
            price_ok = p_date is not None and p_date >= target_price
            val_ok = v_date is not None and v_date >= target_val
            if price_ok and val_ok:
                already_current.add(ts)
            else:
                has_gap.append(ts)

    except Exception as e:
        logger.warning(f"drip: _check_real_gaps error: {e}")
        return list(candidates), already_current

    return has_gap, already_current


def _bump_cycle_seq(ts_codes: list[str]):
    """把已最新的股票 cycle_seq 推到队尾"""
    if not ts_codes:
        return
    from web.backend.database import get_db
    try:
        with get_db() as conn:
            row = conn.execute("SELECT MAX(cycle_seq) FROM drip_sync_progress").fetchone()
            max_seq = (row[0] or 0) + 1
            for ts in ts_codes:
                conn.execute(
                    "UPDATE drip_sync_progress SET cycle_seq = ? WHERE ts_code = ?",
                    (max_seq, ts),
                )
                max_seq += 1
            conn.commit()
    except Exception as e:
        logger.warning(f"drip: _bump_cycle_seq error: {e}")


def _advance_cycle_seq(ts_codes: list[str]):
    """处理完一批后推进 cycle_seq"""
    _bump_cycle_seq(ts_codes)


# ─── 批内结果解析 & 进度更新 ─────────────────────────────────────────────────

def parse_slice_results(
    batch: list[str], phase: str, log_dir: str
) -> tuple[set[str], set[str]]:
    """
    解析隔离日志目录下的 failed CSV，只取 batch 交集。
    phase: "price" | "val"
    返回 (succeeded_set, failed_set)
    """
    batch_set = set(batch)
    csv_name = "failed_hist.csv" if phase == "price" else "failed_factors.csv"
    csv_path = os.path.join(log_dir, csv_name)

    failed_in_batch: set[str] = set()
    if os.path.exists(csv_path):
        try:
            with open(csv_path, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = (row.get("ts_code") or "").strip()
                    if ts in batch_set:
                        failed_in_batch.add(ts)
        except Exception as e:
            logger.warning(f"drip: parse_slice_results error ({csv_name}): {e}")

    succeeded = batch_set - failed_in_batch
    return succeeded, failed_in_batch


def update_stock_progress(
    ts_code: str, phase: str, success: bool, target_date: Optional[str] = None
):
    """逐股更新 drip_sync_progress"""
    from web.backend.database import get_db
    now = sh_timestamp_str()
    try:
        with get_db() as conn:
            if success:
                if phase == "price":
                    conn.execute(
                        "UPDATE drip_sync_progress SET last_price_date = ?, last_sync_at = ?, fail_count = 0 WHERE ts_code = ?",
                        (target_date, now, ts_code),
                    )
                else:
                    conn.execute(
                        "UPDATE drip_sync_progress SET last_val_date = ?, last_sync_at = ?, fail_count = 0 WHERE ts_code = ?",
                        (target_date, now, ts_code),
                    )
            else:
                conn.execute(
                    "UPDATE drip_sync_progress SET fail_count = fail_count + 1, last_fail_at = ? WHERE ts_code = ?",
                    (now, ts_code),
                )
            conn.commit()
    except Exception as e:
        logger.warning(f"drip: update_stock_progress error: {e}")


# ─── 主 Worker ───────────────────────────────────────────────────────────────

def run_one_slice():
    """
    APScheduler 每 8 分钟触发一次。
    非阻塞抢锁 → 选批 → 行情 subprocess → 估值 subprocess → 批内归因 → 释放锁
    """
    global _today_first_run_date, _shutdown_requested

    if _shutdown_requested:
        return

    if not DRIP_STATE["enabled"]:
        DRIP_STATE["status"] = "paused"
        return

    # 非阻塞抢锁：抢不到就跳过本轮（scheduler 或手动 ETL 正在使用）
    acquired = market_db_write_lock.acquire(blocking=False)
    if not acquired:
        logger.info("drip: write lock busy, skipping this slice")
        return

    DRIP_STATE["status"] = "running"
    slice_log_dir: Optional[str] = None
    temp_base_price: Optional[str] = None
    temp_out_val: Optional[str] = None

    try:
        today_str = sh_date_str()

        # 每天首轮：reconcile + refresh priorities
        if _today_first_run_date != today_str:
            reconcile_universe()
            refresh_priorities()
            _today_first_run_date = today_str
            DRIP_STATE["synced_today"] = 0
            DRIP_STATE["current_cycle"] = DRIP_STATE.get("current_cycle", 0) + 1

        target_price, target_val = get_target_dates()

        batch_size = _get_effective_batch_size(40)
        batch = pick_next_batch(batch_size)
        if not batch:
            logger.info("drip: no stocks need updating this slice")
            DRIP_STATE["last_run"] = sh_timestamp_str()
            return

        DRIP_STATE["last_batch_count"] = len(batch)
        ts_stamp = sh_compact_timestamp()
        slice_log_dir = os.path.join(PROJECT_ROOT, "logs", f"drip_slice_{ts_stamp}")
        os.makedirs(slice_log_dir, exist_ok=True)

        # 批量确定各股票的实际缺口（price vs val）
        need_price: list[str] = []
        need_val: list[str] = []
        from web.backend.database import get_market_db
        try:
            with get_market_db() as conn:
                if conn is not None and target_price:
                    placeholders = ",".join(["?" for _ in batch])
                    price_rows = conn.execute(
                        f"SELECT ts_code, MAX(trade_date) FROM daily_price "
                        f"WHERE ts_code IN ({placeholders}) GROUP BY ts_code",
                        batch,
                    ).fetchall()
                    price_latest = {r[0]: r[1] for r in price_rows}

                    val_rows = conn.execute(
                        f"SELECT ts_code, MAX(trade_date) FROM daily_valuation "
                        f"WHERE ts_code IN ({placeholders}) GROUP BY ts_code",
                        batch,
                    ).fetchall()
                    val_latest = {r[0]: r[1] for r in val_rows}

                    for ts in batch:
                        p_date = price_latest.get(ts)
                        v_date = val_latest.get(ts)
                        target_v = target_val or target_price
                        if not p_date or p_date < target_price:
                            need_price.append(ts)
                        if not v_date or v_date < target_v:
                            need_val.append(ts)
                else:
                    need_price = list(batch)
                    need_val = list(batch)
        except Exception as e:
            logger.warning(f"drip: gap determination error: {e}")
            need_price = list(batch)
            need_val = list(batch)

        # 追踪本批真实成功的股票（至少有一个阶段成功）
        actually_synced: set = set()
        had_timeout = False

        # ── 行情阶段 ──────────────────────────────────────────────
        if need_price:
            temp_base_price = _write_temp_base_csv(need_price)
            if temp_base_price:
                rc = _run_price_etl(temp_base_price, slice_log_dir)
                if rc is not None:
                    succeeded, failed = parse_slice_results(need_price, "price", slice_log_dir)
                    for ts in succeeded:
                        update_stock_progress(ts, "price", True, target_price)
                        actually_synced.add(ts)
                    for ts in failed:
                        update_stock_progress(ts, "price", False)
                else:
                    had_timeout = True

        # ── 估值阶段（增量模式，不加 --force-refresh） ───────────
        if need_val:
            temp_out_val = os.path.join(
                tempfile.gettempdir(), f"drip_val_{ts_stamp}.csv"
            )
            rc = _run_val_etl(need_val, temp_out_val, slice_log_dir)
            if rc is not None:
                succeeded, failed = parse_slice_results(need_val, "val", slice_log_dir)
                for ts in succeeded:
                    update_stock_progress(ts, "val", True, target_val)
                    actually_synced.add(ts)
                for ts in failed:
                    update_stock_progress(ts, "val", False)
            else:
                had_timeout = True

        # 推进 cycle_seq（移到队尾）
        _advance_cycle_seq(batch)
        _record_slice_feedback(had_timeout=had_timeout, configured_batch_size=DRIP_STATE["configured_batch_size"])

        # [P2 fix] 只计真实成功的股票，不用整批数量
        DRIP_STATE["synced_today"] = DRIP_STATE.get("synced_today", 0) + len(actually_synced)
        DRIP_STATE["last_run"] = sh_timestamp_str()
        DRIP_STATE["last_error"] = None
        logger.info(f"drip: slice done, processed {len(batch)} stocks")

    except Exception as e:
        logger.error(f"drip: run_one_slice exception: {e}")
        DRIP_STATE["last_error"] = str(e)
    finally:
        DRIP_STATE["status"] = "idle"
        # 清理隔离日志目录
        if slice_log_dir and os.path.exists(slice_log_dir):
            try:
                shutil.rmtree(slice_log_dir, ignore_errors=True)
            except Exception:
                pass
        # 清理临时文件
        for tmp in [temp_base_price, temp_out_val]:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        market_db_write_lock.release()


def _write_temp_base_csv(ts_codes: list[str]) -> Optional[str]:
    """从 stock_industry_base.csv 过滤出子集，写到临时文件"""
    base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    if not os.path.exists(base_path):
        return None

    ts_set = set(ts_codes)
    rows = []
    fieldnames = None
    try:
        with open(base_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for row in reader:
                if (row.get("ts_code") or "").strip() in ts_set:
                    rows.append(row)
    except Exception as e:
        logger.error(f"drip: _write_temp_base_csv read error: {e}")
        return None

    if not rows or not fieldnames:
        return None

    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8", prefix="drip_base_"
        )
        writer = csv.DictWriter(tmp, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        tmp.close()
        return tmp.name
    except Exception as e:
        logger.error(f"drip: _write_temp_base_csv write error: {e}")
        return None


def _get_subprocess_timeout_seconds(default_seconds: int = 600) -> int:
    """
    让子进程超时上限不超过 drip 调度间隔，避免一个 slice 卡住后把下一轮调度直接挤掉。

    规则：
    - 基准默认值仍是 600s
    - 如果读取到 drip_sync_interval_min，则上限收紧为 interval*60 - 60
    - 最小不低于 120s，避免把短网络抖动误判成超时
    """
    timeout_seconds = default_seconds
    try:
        from web.backend.api.settings import get_setting_value
        interval_min = int(get_setting_value("drip_sync_interval_min", "8"))
        if interval_min > 0:
            timeout_seconds = min(timeout_seconds, max(120, interval_min * 60 - 60))
    except Exception:
        pass
    return timeout_seconds


def _run_price_etl(temp_base: str, log_dir: str) -> Optional[int]:
    """运行行情入库脚本，返回 returncode 或 None（异常/超时）"""
    script_path = os.path.join(
        PROJECT_ROOT, "scripts", "etl", "get_market_data_db_robust.py"
    )
    db_path = os.path.join(PROJECT_ROOT, "market.db")
    tmp_out = os.path.join(
        tempfile.gettempdir(), f"drip_price_out_{os.getpid()}.csv"
    )

    cmd = [
        sys.executable, script_path,
        "--base", temp_base,
        "--end-target", "today",
        "--skip-spot", "1",
        "--order", "tx,sina,ts,em",
        "--db", db_path,
        "--out", tmp_out,
        "--log-dir", log_dir,
    ]

    env = _build_env()
    timeout_seconds = _get_subprocess_timeout_seconds()
    try:
        log_file = os.path.join(log_dir, "price_etl.log")
        with open(log_file, "w", encoding="utf-8") as lf:
            result = subprocess.run(
                cmd, stdout=lf, stderr=subprocess.STDOUT,
                cwd=PROJECT_ROOT, env=env, timeout=timeout_seconds,
            )
        return result.returncode
    except subprocess.TimeoutExpired:
        logger.error(f"drip: price ETL timeout (>{timeout_seconds}s)")
        return None
    except Exception as e:
        logger.error(f"drip: price ETL error: {e}")
        return None
    finally:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except Exception:
                pass


def _run_val_etl(
    ts_codes: list[str], tmp_out: str, log_dir: str
) -> Optional[int]:
    """运行估值入库脚本（增量模式，不加 --force-refresh）"""
    script_path = os.path.join(
        PROJECT_ROOT, "scripts", "etl", "build_fundamentals_and_valuation.py"
    )
    db_path = os.path.join(PROJECT_ROOT, "market.db")

    cmd = [
        sys.executable, script_path,
        "--db", db_path,
        "--ts", ",".join(ts_codes),
        "--out", tmp_out,
        "--log-dir", log_dir,
        # 默认增量模式（已有数据的股票跳过 fin_indicators 抓取）
    ]

    env = _build_env()
    timeout_seconds = _get_subprocess_timeout_seconds()
    # 透传数据源配置（P1 fix：etl_source=tushare 时必须显式传 --source）
    try:
        from web.backend.api.settings import get_setting_value
        etl_source = get_setting_value("etl_source", "akshare")
        if etl_source == "tushare" and env.get("TUSHARE_TOKEN"):
            cmd += ["--source", "tushare"]
        elif etl_source == "tushare":
            logger.warning("drip: etl_source=tushare 但未配置 Token，回退 akshare")
    except Exception:
        pass

    try:
        log_file = os.path.join(log_dir, "val_etl.log")
        with open(log_file, "w", encoding="utf-8") as lf:
            result = subprocess.run(
                cmd, stdout=lf, stderr=subprocess.STDOUT,
                cwd=PROJECT_ROOT, env=env, timeout=timeout_seconds,
            )
        return result.returncode
    except subprocess.TimeoutExpired:
        logger.error(f"drip: val ETL timeout (>{timeout_seconds}s)")
        return None
    except Exception as e:
        logger.error(f"drip: val ETL error: {e}")
        return None


def _build_env() -> dict:
    """构建子进程环境变量（注入 TUSHARE_TOKEN）"""
    env = os.environ.copy()
    try:
        from web.backend.api.settings import get_setting_value
        token = get_setting_value("tushare_token", "")
        if token:
            env["TUSHARE_TOKEN"] = token
    except Exception:
        pass
    return env


# ─── 暂停 / 恢复 ─────────────────────────────────────────────────────────────

def pause_drip():
    """用户主动暂停——清除 auto_paused_by（用户意图优先）"""
    DRIP_STATE["enabled"] = False
    DRIP_STATE["auto_paused_by"] = None
    DRIP_STATE["status"] = "paused"
    _save_enabled_state(False)
    logger.info("drip: paused by user")


def resume_drip():
    """用户主动恢复"""
    DRIP_STATE["enabled"] = True
    DRIP_STATE["auto_paused_by"] = None
    DRIP_STATE["status"] = "idle"
    _save_enabled_state(True)
    logger.info("drip: resumed by user")


def pause_drip_for_manual_etl():
    """手动 ETL 自动暂停 drip（仅做标记，不阻塞事件循环）"""
    if DRIP_STATE["enabled"]:
        DRIP_STATE["enabled"] = False
        DRIP_STATE["auto_paused_by"] = "manual_etl"
        logger.info("drip: auto-paused for manual ETL")


def maybe_resume_drip_after_manual_etl():
    """手动 ETL 完成后条件恢复（仅当 auto_paused_by 仍为 manual_etl 时）"""
    if DRIP_STATE.get("auto_paused_by") == "manual_etl":
        DRIP_STATE["enabled"] = True
        DRIP_STATE["auto_paused_by"] = None
        DRIP_STATE["status"] = "idle"
        logger.info("drip: auto-resumed after manual ETL")


def _save_enabled_state(enabled: bool):
    """持久化 enabled 状态到 system_settings"""
    try:
        from web.backend.database import get_db
        with get_db() as conn:
            conn.execute(
                "INSERT INTO system_settings (key, value) VALUES ('drip_sync_enabled', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if enabled else "0",),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"drip: _save_enabled_state error: {e}")


# ─── 周期重置 ────────────────────────────────────────────────────────────────

def reset_cycle():
    """重置滴灌轮次：所有股票 cycle_seq=0, fail_count=0"""
    from web.backend.database import get_db
    try:
        with get_db() as conn:
            conn.execute("UPDATE drip_sync_progress SET cycle_seq = 0, fail_count = 0")
            conn.commit()
        DRIP_STATE["current_cycle"] = 0
        DRIP_STATE["synced_today"] = 0
        logger.info("drip: cycle reset")
    except Exception as e:
        logger.error(f"drip: reset_cycle error: {e}")


# ─── 关闭 ────────────────────────────────────────────────────────────────────

def shutdown_drip():
    """优雅关闭：标记停止，等待当前 slice 完成（最多 60s）"""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("drip: shutdown requested, waiting for current slice...")
    acquired = market_db_write_lock.acquire(timeout=60)
    if acquired:
        market_db_write_lock.release()
    logger.info("drip: shutdown complete")


# ─── 队列摘要（供 API 查询） ─────────────────────────────────────────────────

def get_queue_summary() -> dict:
    """
    返回队列摘要。
    candidates = priority < 999 AND fail_count < 5 的候选数（drip_sync_progress 队列视角，
    非 market.db 二次校正后的真实缺口数，前端应标注"队列候选数"而非"待处理"）。
    """
    from web.backend.database import get_db
    try:
        with get_db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM drip_sync_progress WHERE priority < 999"
            ).fetchone()[0]
            candidates = conn.execute(
                "SELECT COUNT(*) FROM drip_sync_progress "
                "WHERE priority < 999 AND fail_count < 5"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM drip_sync_progress WHERE fail_count >= 5"
            ).fetchone()[0]
        return {"total": total, "candidates": candidates, "failed_stocks": failed}
    except Exception:
        return {"total": 0, "candidates": 0, "failed_stocks": 0}
