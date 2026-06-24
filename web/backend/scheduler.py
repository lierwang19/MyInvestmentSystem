import os
import sys
import logging
import subprocess
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Setup logging
logger = logging.getLogger("scheduler")
logger.setLevel(logging.INFO)

from web.backend.config import PROJECT_ROOT
from web.backend.time_utils import sh_date_str, sh_now, sh_today

# Last run status for each job (cleared on restart)
# status 取值: "running" | "ok" | "error" | "skipped"
SCHEDULER_LAST_RUN: dict = {}

# Pipeline sequencing Events（初始均为 set，表示"未在运行"）
# market_update 启动时 clear → 完成/失败后 set
# valuation_build 等 _market_update_done，自身启动时 clear _valuation_done → 完成/失败后 set
# factor_build 等 _valuation_done
_market_update_done = threading.Event()
_market_update_done.set()
_valuation_done = threading.Event()
_valuation_done.set()

# Default schedule config
DEFAULT_SCHEDULES = {
    "market_update": {
        "name": "行情数据入库",
        "hour": 17, "minute": 0,
        "enabled": True,
        "description": "收盘后拉取日线数据写入 market.db"
    },
    "factor_build": {
        "name": "因子计算",
        "hour": 17, "minute": 15,
        "enabled": True,
        "description": "从 market.db 重新计算 PB/PE 因子，生成最新 market_enriched.csv（建议在估值构建完成后运行，推荐 17:35）"
    },
    "valuation_build": {
        "name": "估值数据构建",
        "hour": 17, "minute": 10,
        "enabled": True,
        "description": "补充持仓+优选池股票的 PB/PE 估值数据（daily_valuation 表）"
    },
    "asset_snapshot": {
        "name": "资产快照",
        "hour": 17, "minute": 20,
        "enabled": True,
        "description": "记录当日各账户资产净值"
    },
    "scoring": {
        "name": "评分选股",
        "hour": 17, "minute": 30,
        "enabled": True,
        "description": "根据最新数据刷新股票池评分"
    },
    "strategy_run": {
        "name": "策略信号生成",
        "hour": 17, "minute": 35,
        "enabled": True,
        "description": "基于最新评分和持仓生成买卖建议"
    },
    "monthly_sync": {
        "name": "大盘月度同步",
        "hour": 18, "minute": 0,
        "enabled": True,
        "description": "同步沪深大盘月度数据"
    },
    "ipo_calendar_sync": {
        "name": "股票打新日历同步",
        "hour": 7, "minute": 30,
        "enabled": True,
        "description": "同步今天到未来 7 天的沪深A股、北交所打新日历"
    },
    "ai_briefing": {
        "name": "AI 持仓简报",
        "hour": 18, "minute": 30,
        "enabled": False,
        "description": "AI 扫描所有持仓股票，生成每日投资简报（默认关闭，约 30s/只）"
    },
}

# Job function mapping
JOB_FUNCTIONS = {}


def _scheduler_hm() -> str:
    return sh_now().strftime("%H:%M")


def _scheduler_day_key() -> str:
    return sh_now().strftime("%Y%m%d")


# ─── Calibrator helpers ────────────────────────────────────────────────────────

def _build_calibrator_base(base_csv: str, market_db: str):
    """
    为 scheduler 日终行情校准器构建补漏 base CSV。
    返回三元组 (ok, temp_path_or_none, info_msg)：
      - (True,  path,  msg)  → 覆盖率 >90%，有待补股票，临时 CSV 已写好
      - (True,  None,  msg)  → 覆盖率 >90% 且无缺口，正常跳过
      - (False, None,  msg)  → 覆盖率 <90% 或读取失败，应走全量 ETL
    """
    import csv as _csv
    import sqlite3
    import tempfile

    # 1. 读取 universe
    universe_rows = []
    seen: set = set()
    try:
        with open(base_csv, encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                code = (row.get("ts_code") or "").strip()
                if code and not code.endswith(".BJ") and code not in seen:
                    universe_rows.append(row)
                    seen.add(code)
    except Exception as e:
        return (False, None, f"无法读取 stock_industry_base.csv：{e}")

    if not universe_rows:
        return (False, None, "stock_industry_base.csv 为空或格式错误")

    universe_codes = {r["ts_code"] for r in universe_rows}

    # 2. 查 market.db 最新交易日覆盖情况
    try:
        con = sqlite3.connect(market_db, timeout=30)
        row = con.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
        latest_date = row[0] if row else None
        if not latest_date:
            con.close()
            return (False, None, "market.db 中 daily_price 无数据，应走全量 ETL")
        covered = {
            r[0] for r in con.execute(
                "SELECT DISTINCT ts_code FROM daily_price WHERE trade_date = ?",
                (latest_date,)
            ).fetchall()
        }
        con.close()
    except Exception as e:
        return (False, None, f"读取 market.db 失败：{e}")

    # 3. 计算覆盖率
    total = len(universe_codes)
    covered_in_universe = covered & universe_codes
    coverage_ratio = len(covered_in_universe) / total if total else 0

    if coverage_ratio < 0.9:
        return (False, None, f"覆盖率 {coverage_ratio:.0%}（<90%），走全量 ETL")

    missing = universe_codes - covered_in_universe
    if not missing:
        return (True, None, f"全市场已覆盖（{len(covered_in_universe)}/{total}），跳过")

    # 4. 写临时 base CSV（仅含缺失股票）
    missing_rows = [r for r in universe_rows if r["ts_code"] in missing]
    fieldnames = list(universe_rows[0].keys())
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False,
        encoding="utf-8", prefix="sched_fill_"
    )
    writer = _csv.DictWriter(tmp, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(missing_rows)
    tmp.close()
    return (True, tmp.name, f"补漏模式：{len(missing)}/{total}（覆盖率 {coverage_ratio:.0%}）")


def _get_strict_valuation_coverage(market_db: str, base_csv: str):
    """
    查 daily_valuation 表的真实缺口（不 fallback 到 daily_snapshot）。
    target_date = MAX(trade_date) FROM daily_price（对齐行情口径）。
    返回 (missing_ts_codes: list, target_date: str | None)。
    """
    import csv as _csv
    import sqlite3

    # 1. 读取 universe
    universe_codes: set = set()
    try:
        with open(base_csv, encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                code = (row.get("ts_code") or "").strip()
                if code and not code.endswith(".BJ"):
                    universe_codes.add(code)
    except Exception:
        return [], None

    if not universe_codes:
        return [], None

    # 2. 查 market.db
    try:
        con = sqlite3.connect(market_db, timeout=30)
        row = con.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
        target_date = row[0] if row else None
        if not target_date:
            con.close()
            return [], None
        covered_val = {
            r[0] for r in con.execute(
                "SELECT DISTINCT ts_code FROM daily_valuation WHERE trade_date = ?",
                (target_date,)
            ).fetchall()
        }
        con.close()
    except Exception as e:
        logger.error(f"_get_strict_valuation_coverage: {e}")
        return [], None

    missing = list(universe_codes - covered_val)
    return missing, target_date


def run_daily_market_update():
    """Run get_market_data_db_robust.py to fetch daily prices and update market_enriched.csv"""
    from web.backend.services.trading_calendar import is_a_trading_day
    if not is_a_trading_day():
        logger.info("market_update: 今日非交易日，跳过")
        SCHEDULER_LAST_RUN["market_update"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "non_trading_day",
            "detail": "今日非交易日",
        }
        return

    logger.info("Starting Daily Market Data Update...")

    # [P0] 占用 Event，让 valuation_build 等待
    _market_update_done.clear()
    SCHEDULER_LAST_RUN["market_update"] = {
        "time": _scheduler_hm(),
        "status": "running",
        "detail": "运行中",
    }

    # 等待 drip_sync 释放写锁（最多 15 分钟）
    # [P0] 超时后必须中止，不能在未持锁情况下写 market.db
    lock_acquired = False
    try:
        from web.backend.drip_sync import market_db_write_lock
        lock_acquired = market_db_write_lock.acquire(timeout=900)
    except Exception as e:
        logger.error(f"market_update: 获取写锁异常：{e}")

    if not lock_acquired:
        logger.error("market_update: 等待写锁超时（15 min），已跳过，保持写入互斥")
        SCHEDULER_LAST_RUN["market_update"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "lock_timeout",
            "detail": "等待 drip 写锁超时（15 min），已跳过",
        }
        _market_update_done.set()
        return

    script_path = os.path.join(PROJECT_ROOT, "scripts", "etl", "get_market_data_db_robust.py")
    log_file = os.path.join(PROJECT_ROOT, "logs", f"scheduler_daily_{_scheduler_day_key()}.log")
    market_db = os.path.join(PROJECT_ROOT, "market.db")
    base_csv = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")

    # Ensure logs dir exists
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    fill_base: str | None = None  # 临时补漏 base CSV 路径，finally 中清理

    try:
        # [Calibrator] 校准器：先检查覆盖率，>90% 走补漏，否则全量
        ok_fill, fill_base, fill_msg = _build_calibrator_base(base_csv, market_db)

        if ok_fill and fill_base is None:
            # 已全覆盖，drip 已完成全部同步，跳过本次 ETL
            logger.info(f"market_update calibrator: {fill_msg}")
            SCHEDULER_LAST_RUN["market_update"] = {
                "time": _scheduler_hm(),
                "status": "ok",
                "detail": f"drip已覆盖，跳过",
            }
            return

        if ok_fill and fill_base:
            # 补漏模式：只同步缺失的股票
            logger.info(f"market_update calibrator: {fill_msg}")
            cmd = [
                sys.executable, script_path,
                "--base", fill_base,
                "--skip-spot", "1",
                "--order", "tx,sina,ts,em",
                "--db", market_db,
                "--out", os.path.join(PROJECT_ROOT, "market_enriched.csv"),
            ]
        else:
            # 全量模式：覆盖率不足 90% 或读取失败
            if fill_msg:
                logger.info(f"market_update full mode: {fill_msg}")
            cmd = [
                sys.executable, script_path,
                "--end-target", "today",
                "--skip-spot", "1",
                "--order", "tx,sina,ts,em",
                "--db", market_db,
                "--out", os.path.join(PROJECT_ROOT, "market_enriched.csv"),
            ]

        with open(log_file, "w", encoding="utf-8") as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True, cwd=PROJECT_ROOT)
        logger.info("Daily Market Data Update Completed Successfully.")
        SCHEDULER_LAST_RUN["market_update"] = {
            "time": _scheduler_hm(),
            "status": "ok",
            "detail": fill_msg if (ok_fill and fill_base) else "全量完成",
        }
    except subprocess.CalledProcessError as e:
        logger.error(f"Daily Market Data Update Failed: {e}")
        SCHEDULER_LAST_RUN["market_update"] = {
            "time": _scheduler_hm(),
            "status": "error",
            "detail": str(e),
        }
    except Exception as e:
        logger.error(f"Daily Market Data Update Error: {e}")
        SCHEDULER_LAST_RUN["market_update"] = {
            "time": _scheduler_hm(),
            "status": "error",
            "detail": str(e),
        }
    finally:
        # 清理补漏临时 base CSV
        if fill_base and os.path.exists(fill_base):
            try:
                os.remove(fill_base)
            except Exception:
                pass
        # 释放写锁
        try:
            if lock_acquired:
                from web.backend.drip_sync import market_db_write_lock
                market_db_write_lock.release()
        except Exception:
            pass
        # 无论成功/失败，都释放 Event，让 valuation_build 可以继续
        _market_update_done.set()

def run_monthly_market_sync():
    """Call internal API function to sync monthly market data"""
    from web.backend.services.trading_calendar import is_a_trading_day
    if not is_a_trading_day():
        logger.info("monthly_sync: 今日非交易日，跳过")
        SCHEDULER_LAST_RUN["monthly_sync"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "non_trading_day",
            "detail": "今日非交易日",
        }
        return

    logger.info("Starting Monthly Market Data Sync...")
    try:
        # Import inside function to avoid circular imports if any
        from web.backend.api.market import sync_market_data
        
        result = sync_market_data()
        if result.get("status") == "success":
             logger.info(f"Monthly Market Data Sync Success: {result.get('message')}")
        else:
             logger.error(f"Monthly Market Data Sync Failed: {result.get('message')}")
             
    except Exception as e:
        logger.error(f"Monthly Market Data Sync Exception: {e}")

def run_factor_build():
    """Run make_market_enriched_from_db.py to rebuild PB/PE factors and market_enriched.csv"""
    from web.backend.services.trading_calendar import is_a_trading_day
    if not is_a_trading_day():
        logger.info("factor_build: 今日非交易日，跳过")
        SCHEDULER_LAST_RUN["factor_build"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "non_trading_day",
            "detail": "今日非交易日",
        }
        return

    logger.info("Starting Factor Build (market_enriched.csv regeneration)...")
    SCHEDULER_LAST_RUN["factor_build"] = {
        "time": _scheduler_hm(),
        "status": "running",
        "detail": "运行中",
    }

    # [P0] 等待 valuation_build 完成后再读 daily_valuation（最多等 40 分钟）
    # [P1] 超时则跳过，不能用依赖未满足的数据生成 market_enriched.csv
    if not _valuation_done.wait(timeout=40 * 60):
        logger.error("Factor Build: valuation_build 仍在运行（等待 40 min 超时），跳过本次 enriched 重算")
        SCHEDULER_LAST_RUN["factor_build"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "detail": "上游估值构建超时未完成，已跳过",
        }
        return

    # 上游失败时跳过，避免用残缺 daily_valuation 生成 market_enriched.csv
    # 白名单放行（其余一律阻断）：
    #   ok                           → 估值补全成功
    #   skipped + no_missing         → drip 已全覆盖，估值校准正常跳过
    #   None（进程重启，无记录）       → 保守放行，避免阻断正常启动后首次运行
    # 阻断：
    #   error                        → 估值 ETL 脚本报错
    #   skipped + upstream_failed    → 上游行情入库失败/超时
    #   skipped + lock_timeout       → 写锁等待超时
    #   skipped + no_target_date     → market.db 无目标交易日（首次部署 / 数据异常）
    val_entry = SCHEDULER_LAST_RUN.get("valuation_build", {})
    valuation_status = val_entry.get("status")
    valuation_reason = val_entry.get("reason")
    ALLOWED = {
        ("ok", None),   # 估值补全成功，或 drip 已全覆盖正常跳过
        (None, None),   # 进程重启无记录，保守放行
    }
    blocked = (valuation_status, valuation_reason) not in ALLOWED
    if blocked:
        logger.warning(
            f"Factor Build: valuation_build status={valuation_status!r} reason={valuation_reason!r}，跳过本次 enriched 重算"
        )
        SCHEDULER_LAST_RUN["factor_build"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "detail": f"上游估值构建 {valuation_status}（{valuation_reason}），已跳过",
        }
        return

    script_path = os.path.join(PROJECT_ROOT, "scripts", "etl", "make_market_enriched_from_db.py")
    log_file = os.path.join(PROJECT_ROOT, "logs", f"scheduler_factor_{_scheduler_day_key()}.log")

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    cmd = [
        sys.executable,
        script_path,
        "--base", os.path.join(PROJECT_ROOT, "stock_industry_base.csv"),
        "--db", os.path.join(PROJECT_ROOT, "market.db"),
        "--out", os.path.join(PROJECT_ROOT, "market_enriched.csv"),
        "--n-low-days", "252",
        "--use-tushare", "0",  # 不依赖 Tushare，用 DB 内数据
    ]

    try:
        with open(log_file, "w", encoding="utf-8") as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True, cwd=PROJECT_ROOT)
        logger.info("Factor Build Completed Successfully.")
        SCHEDULER_LAST_RUN["factor_build"] = {
            "time": _scheduler_hm(),
            "status": "ok",
            "detail": "完成",
        }
    except subprocess.CalledProcessError as e:
        logger.error(f"Factor Build Failed: {e}")
        SCHEDULER_LAST_RUN["factor_build"] = {
            "time": _scheduler_hm(),
            "status": "error",
            "detail": str(e),
        }
    except Exception as e:
        logger.error(f"Factor Build Error: {e}")
        SCHEDULER_LAST_RUN["factor_build"] = {
            "time": _scheduler_hm(),
            "status": "error",
            "detail": str(e),
        }


def run_valuation_build():
    """校准器：补全 daily_valuation 真实缺口（build_fundamentals_and_valuation.py）"""
    # 节假日 skip 必须在 _valuation_done.clear() 之前，避免 factor_build 因 Event 被清空而阻塞
    from web.backend.services.trading_calendar import is_a_trading_day
    if not is_a_trading_day():
        logger.info("valuation_build: 今日非交易日，跳过")
        SCHEDULER_LAST_RUN["valuation_build"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "non_trading_day",
            "detail": "今日非交易日",
        }
        return

    logger.info("Starting Valuation Build calibrator (strict daily_valuation coverage)...")

    # [Race Fix] 先占用 _valuation_done，再等上游，避免 factor_build 在等待窗口内误判 Event 已 set
    _valuation_done.clear()

    # 立即写 running 状态（在等待上游期间也应显示为 running，让前端卡片反映真实链路状态）
    SCHEDULER_LAST_RUN["valuation_build"] = {
        "time": _scheduler_hm(),
        "status": "running",
        "detail": "等待上游行情入库完成…",
    }

    # [P0] 等待行情入库完成后再写 daily_valuation（最多等 40 分钟）
    if not _market_update_done.wait(timeout=40 * 60):
        logger.error("Valuation Build: market_update 仍在运行（等待 40 min 超时），跳过本次估值构建")
        SCHEDULER_LAST_RUN["valuation_build"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "upstream_failed",
            "detail": "上游行情入库超时未完成，已跳过",
        }
        return

    # 上游失败时跳过，避免基于旧/残缺行情计算估值
    market_status = SCHEDULER_LAST_RUN.get("market_update", {}).get("status")
    if market_status not in ("ok", None):
        logger.warning(f"Valuation Build: market_update 状态为 {market_status!r}，跳过本次估值构建")
        SCHEDULER_LAST_RUN["valuation_build"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "upstream_failed",
            "detail": f"上游行情入库 {market_status}，已跳过",
        }
        return

    # 上游就绪，更新 detail 为实际执行中
    SCHEDULER_LAST_RUN["valuation_build"]["detail"] = "执行中"

    # 等待 drip_sync 释放写锁（由 run_daily_market_update 转让后立即可获得）
    # [P0] 超时后必须中止，不能在未持锁情况下写 market.db
    lock_acquired_val = False
    try:
        from web.backend.drip_sync import market_db_write_lock
        lock_acquired_val = market_db_write_lock.acquire(timeout=900)
    except Exception as e:
        logger.error(f"valuation_build: 获取写锁异常：{e}")

    if not lock_acquired_val:
        logger.error("valuation_build: 等待写锁超时（15 min），已跳过，保持写入互斥")
        SCHEDULER_LAST_RUN["valuation_build"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "lock_timeout",
            "detail": "等待 drip 写锁超时（15 min），已跳过",
        }
        _valuation_done.set()
        return

    script_path = os.path.join(PROJECT_ROOT, "scripts", "etl", "build_fundamentals_and_valuation.py")
    log_file = os.path.join(PROJECT_ROOT, "logs", f"scheduler_valuation_{_scheduler_day_key()}.log")
    market_db = os.path.join(PROJECT_ROOT, "market.db")
    base_csv = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    import tempfile
    import time as _t
    tmp_out = os.path.join(tempfile.gettempdir(), f"val_sched_{int(_t.time())}.csv")

    try:
        # 1. [Calibrator] 查 daily_valuation 真实缺口（严格口径，不 fallback daily_snapshot）
        ts_codes, target_date = _get_strict_valuation_coverage(market_db, base_csv)

        if target_date is None:
            logger.warning("Valuation Build: 无法获取目标日期（market.db 可能为空），跳过")
            SCHEDULER_LAST_RUN["valuation_build"] = {
                "time": _scheduler_hm(),
                "status": "skipped",
                "reason": "no_target_date",
                "detail": "market.db 无行情数据",
            }
            return

        if not ts_codes:
            logger.info(f"Valuation Build: {target_date} 估值全覆盖，跳过")
            SCHEDULER_LAST_RUN["valuation_build"] = {
                "time": _scheduler_hm(),
                "status": "ok",
                "detail": f"drip已覆盖 {target_date}，跳过",
            }
            return

        logger.info(f"Valuation Build: {target_date} 缺 {len(ts_codes)} 只，开始补全")

        # 2. 构建命令（增量模式，不加 --force-refresh；drip 全天已拉增量，校准器只补漏）
        cmd = [
            sys.executable, script_path,
            "--db", market_db,
            "--ts", ",".join(ts_codes),
            "--out", tmp_out,
        ]
        env = os.environ.copy()
        try:
            from web.backend.api.settings import get_setting_value
            token = get_setting_value("tushare_token", "")
            etl_source = get_setting_value("etl_source", "akshare")
            if token:
                env["TUSHARE_TOKEN"] = token
            if etl_source == "tushare" and token:
                cmd += ["--source", "tushare"]
            elif etl_source == "tushare" and not token:
                logger.warning("scheduler: etl_source=tushare 但未配置 Tushare Token，回退使用 akshare")
        except Exception:
            pass

        # 3. 执行
        with open(log_file, "w", encoding="utf-8") as f:
            result = subprocess.run(
                cmd, stdout=f, stderr=subprocess.STDOUT,
                cwd=PROJECT_ROOT, env=env, timeout=7200
            )
        if result.returncode == 0:
            logger.info(f"Valuation Build Completed ({len(ts_codes)} stocks missing for {target_date}).")
            SCHEDULER_LAST_RUN["valuation_build"] = {
                "time": _scheduler_hm(),
                "status": "ok",
                "detail": f"补全 {len(ts_codes)} 只（{target_date}）",
            }
        else:
            logger.error("Valuation Build Failed (non-zero return code).")
            SCHEDULER_LAST_RUN["valuation_build"] = {
                "time": _scheduler_hm(),
                "status": "error",
                "detail": "returncode != 0",
            }
    except subprocess.TimeoutExpired:
        logger.error("Valuation Build Timeout (> 120 min).")
        SCHEDULER_LAST_RUN["valuation_build"] = {
            "time": _scheduler_hm(),
            "status": "error",
            "detail": "超时",
        }
    except Exception as e:
        logger.error(f"Valuation Build Error: {e}")
        SCHEDULER_LAST_RUN["valuation_build"] = {
            "time": _scheduler_hm(),
            "status": "error",
            "detail": str(e),
        }
    finally:
        try:
            if lock_acquired_val:
                from web.backend.drip_sync import market_db_write_lock
                market_db_write_lock.release()
        except Exception:
            pass
        # [P0] 无论成功/失败/异常，都释放 Event，让 factor_build 可以继续
        _valuation_done.set()
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except Exception:
                pass


def run_daily_scoring():
    """Run scoring to refresh the stock pool after market data update"""
    from web.backend.services.trading_calendar import is_a_trading_day
    if not is_a_trading_day():
        logger.info("scoring: 今日非交易日，跳过")
        SCHEDULER_LAST_RUN["scoring"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "non_trading_day",
            "detail": "今日非交易日",
        }
        return

    logger.info("Starting Daily Scoring...")
    
    try:
        import scoring
        result = scoring.run_scoring(
            config_path=os.path.join(PROJECT_ROOT, "config", "scoring.yaml"),
            base_path=os.path.join(PROJECT_ROOT, "stock_industry_base.csv"),
            enriched_path=os.path.join(PROJECT_ROOT, "market_enriched.csv"),
            user_db_path=os.path.join(PROJECT_ROOT, "user_data.db"),
            min_score=70,
            write_csv=False,
        )
        logger.info(f"Daily Scoring Completed: {result.get('pool_count', 0)} stocks in pool")
    except Exception as e:
        logger.error(f"Daily Scoring Error: {e}")

def run_daily_strategy():
    """Run strategy engine for all accounts to generate signals and decisions after scoring"""
    # 账户级任务：A 股或港股任一开市即需生成信号，避免港股交易日漏跑策略
    from web.backend.services.trading_calendar import is_a_trading_day, is_hk_trading_day
    if not (is_a_trading_day() or is_hk_trading_day()):
        logger.info("strategy_run: 今日 A 股与港股均非交易日，跳过")
        SCHEDULER_LAST_RUN["strategy_run"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "non_trading_day",
            "detail": "今日 A 股与港股均非交易日",
        }
        return

    logger.info("Starting Daily Strategy Run...")
    try:
        import engine as strategy_engine
        from web.backend.database import get_db
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM accounts ORDER BY created_at ASC")
            account_ids = [row["id"] for row in c.fetchall()]

        if not account_ids:
            logger.warning("Daily Strategy Run: no accounts found, running without account context")
            account_ids = [""]

        for account_id in account_ids:
            result = strategy_engine.run_strategy(
                strategy_id="dashu_v1",
                account_id=account_id,
                project_root=PROJECT_ROOT,
            )
            if result.error:
                logger.error(f"Daily Strategy Run Failed (account={account_id}): {result.error}")
            else:
                logger.info(
                    f"Daily Strategy Run Completed (account={account_id}): "
                    f"{len(result.signals)} signals, {len(result.decisions)} decisions"
                )
    except Exception as e:
        logger.error(f"Daily Strategy Run Error: {e}")

def run_daily_asset_snapshot():
    """Take asset snapshot for all accounts to build net worth curve"""
    # 账户级任务：A 股或港股任一开市即需记录净值，避免港股交易日漏记快照
    from web.backend.services.trading_calendar import is_a_trading_day, is_hk_trading_day
    if not (is_a_trading_day() or is_hk_trading_day()):
        logger.info("asset_snapshot: 今日 A 股与港股均非交易日，跳过")
        SCHEDULER_LAST_RUN["asset_snapshot"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "non_trading_day",
            "detail": "今日 A 股与港股均非交易日",
        }
        return

    logger.info("Starting Daily Asset Snapshot...")
    try:
        from web.backend.api.assets import snapshot_assets
        result = snapshot_assets(account_id="all")
        if result.get("status") == "success":
            logger.info(f"Asset Snapshot Success: {len(result.get('data', []))} accounts")
        else:
            logger.error(f"Asset Snapshot Failed: {result}")
    except Exception as e:
        logger.error(f"Asset Snapshot Error: {e}")


def run_ipo_calendar_sync():
    """同步 IPO 日历（today ~ today+7d），不依赖 A 股交易日"""
    logger.info("Starting IPO Calendar Sync...")
    SCHEDULER_LAST_RUN["ipo_calendar_sync"] = {
        "time": _scheduler_hm(),
        "status": "running",
        "detail": "运行中",
    }

    try:
        from datetime import timedelta
        from scripts.etl.sync_ipo_calendar import sync_ipo_calendar

        start_date = sh_today()
        end_date = start_date + timedelta(days=7)
        result = sync_ipo_calendar(start_date, end_date)
        rows_written = int(result.get("rows_written", 0))
        detail = f"完成，写入 {rows_written} 条"
        logger.info(
            "IPO Calendar Sync Completed: %s ~ %s, rows=%s",
            result.get("start_date"), result.get("end_date"), rows_written
        )
        SCHEDULER_LAST_RUN["ipo_calendar_sync"] = {
            "time": _scheduler_hm(),
            "status": "ok",
            "detail": detail,
        }
    except Exception as e:
        logger.error(f"IPO Calendar Sync Error: {e}")
        SCHEDULER_LAST_RUN["ipo_calendar_sync"] = {
            "time": _scheduler_hm(),
            "status": "error",
            "detail": str(e),
        }

def run_ai_briefing():
    """AI 持仓简报：扫描所有持仓股，逐只生成简报并存库。"""
    from web.backend.services.trading_calendar import is_a_trading_day
    if not is_a_trading_day():
        logger.info("ai_briefing: 今日非交易日，跳过")
        SCHEDULER_LAST_RUN["ai_briefing"] = {
            "time": _scheduler_hm(),
            "status": "skipped",
            "reason": "non_trading_day",
            "detail": "今日非交易日",
        }
        return

    logger.info("Starting AI Holdings Briefing...")
    SCHEDULER_LAST_RUN["ai_briefing"] = {
        "time": _scheduler_hm(),
        "status": "running",
        "detail": "运行中",
    }
    try:
        from web.backend.api.ai_briefing import run_holdings_briefing
        result = run_holdings_briefing()
        status = result.get("status", "error")
        detail = (
            f"完成 {result.get('completed', 0)}/{result.get('total', 0)} 只"
            if status == "done"
            else result.get("detail", str(result))
        )
        SCHEDULER_LAST_RUN["ai_briefing"] = {
            "time": _scheduler_hm(),
            "status": "ok" if status == "done" else "error",
            "detail": detail,
        }
        logger.info(f"AI Holdings Briefing finished: {detail}")
    except Exception as e:
        logger.error(f"AI Briefing Scheduler Error: {e}")
        SCHEDULER_LAST_RUN["ai_briefing"] = {
            "time": _scheduler_hm(),
            "status": "error",
            "detail": str(e),
        }
# Map job_id -> function
JOB_FUNCTIONS = {
    "market_update": run_daily_market_update,
    "valuation_build": run_valuation_build,
    "factor_build": run_factor_build,
    "asset_snapshot": run_daily_asset_snapshot,
    "scoring": run_daily_scoring,
    "strategy_run": run_daily_strategy,
    "monthly_sync": run_monthly_market_sync,
    "ipo_calendar_sync": run_ipo_calendar_sync,
    "ai_briefing": run_ai_briefing,
}

# Global scheduler reference
_scheduler = None

def get_schedule_config():
    """Load schedule config from DB settings, falling back to defaults"""
    import json
    try:
        from web.backend.database import get_db
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM system_settings WHERE key = 'scheduler_config'")
            row = c.fetchone()
        if row:
            saved = json.loads(row[0])
            # Merge with defaults (add any new jobs not yet in saved config)
            config = {}
            for job_id, defaults in DEFAULT_SCHEDULES.items():
                if job_id in saved:
                    merged = {**defaults, **saved[job_id]}
                    config[job_id] = merged
                else:
                    config[job_id] = defaults.copy()
            return config
    except Exception as e:
        logger.warning(f"Failed to load scheduler config from DB: {e}")
    
    return {k: v.copy() for k, v in DEFAULT_SCHEDULES.items()}

def save_schedule_config(config):
    """Save schedule config to DB settings"""
    import json
    try:
        from web.backend.database import get_db
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO system_settings (key, value) VALUES ('scheduler_config', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (json.dumps(config, ensure_ascii=False),))
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to save scheduler config: {e}")
        return False

def _get_scheduler_tz():
    """从系统设置读取时区，返回 pytz timezone 对象，默认 Asia/Shanghai"""
    try:
        import pytz
        from web.backend.api.settings import get_setting_value
        tz_name = get_setting_value("system_timezone", "Asia/Shanghai") or "Asia/Shanghai"
        return pytz.timezone(tz_name)
    except Exception:
        try:
            import pytz
            return pytz.timezone("Asia/Shanghai")
        except Exception:
            return None


def apply_schedule(scheduler=None, tz=None):
    """Apply current schedule config to the scheduler"""
    global _scheduler
    if scheduler:
        _scheduler = scheduler
    if not _scheduler:
        return

    if tz is None:
        tz = _get_scheduler_tz()

    config = get_schedule_config()

    for job_id, cfg in config.items():
        func = JOB_FUNCTIONS.get(job_id)
        if not func:
            continue

        # Remove existing job if any
        try:
            _scheduler.remove_job(job_id)
        except Exception:
            pass

        if cfg.get("enabled", True):
            trigger_kwargs = {"hour": cfg["hour"], "minute": cfg["minute"]}
            if tz is not None:
                trigger_kwargs["timezone"] = tz
            _scheduler.add_job(
                func,
                CronTrigger(**trigger_kwargs),
                id=job_id,
                name=cfg.get("name", job_id),
                replace_existing=True
            )
            logger.info(f"Scheduled '{cfg.get('name', job_id)}' at {cfg['hour']:02d}:{cfg['minute']:02d} ({tz})")
        else:
            logger.info(f"Skipped '{cfg.get('name', job_id)}' (disabled)")


def start_scheduler():
    global _scheduler
    tz = _get_scheduler_tz()
    _scheduler = BackgroundScheduler(timezone=tz)
    apply_schedule(_scheduler, tz=tz)

    # 初始化滴灌同步
    try:
        from web.backend.drip_sync import init_drip_sync, run_one_slice
        init_drip_sync()
        from apscheduler.triggers.interval import IntervalTrigger
        # [P2 fix] 从设置读取调度间隔，默认 8 分钟
        try:
            from web.backend.api.settings import get_setting_value
            interval_min = int(get_setting_value("drip_sync_interval_min", "8"))
            if interval_min < 1:
                interval_min = 8
        except Exception:
            interval_min = 8
        _scheduler.add_job(
            run_one_slice,
            IntervalTrigger(minutes=interval_min),
            id="drip_sync",
            name="持续补数（切片滴灌）",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        logger.info(f"Drip sync job registered (every {interval_min} min)")
    except Exception as e:
        logger.error(f"Failed to init drip sync: {e}")

    _scheduler.start()
    logger.info("Scheduler started")
    return _scheduler
