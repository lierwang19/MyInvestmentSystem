from __future__ import annotations
from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile
import subprocess
import os
import sys
import logging
from pathlib import Path, PurePosixPath
import re

logger = logging.getLogger(__name__)
from web.backend.security import require_admin_token

router = APIRouter(dependencies=[Depends(require_admin_token)])

# Global state to track refresh status (Primitive locking)
REFRESH_STATE = {
    "status": "idle", # idle, running
    "last_run": None,
    "last_error": None
}

from web.backend.config import PROJECT_ROOT, MARKET_DB_PATH, USER_DB_PATH
from web.backend.time_utils import sh_compact_timestamp, sh_now, sh_timestamp_str
from web.backend.upload_security import MAX_GENERIC_UPLOAD_BYTES, read_limited_upload

_HK_CODE_RE = re.compile(r"^\d{5}\.HK$")
_YYYYMMDD_RE = re.compile(r"^\d{8}$")

def run_market_refresh_script():
    global REFRESH_STATE
    REFRESH_STATE["status"] = "running"
    REFRESH_STATE["last_error"] = None
    
    script_path = os.path.join(PROJECT_ROOT, "scripts", "etl", "make_market_enriched_from_db.py")
    base_file = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    db_file = MARKET_DB_PATH
    
    # Check dependencies
    if not os.path.exists(db_file):
        REFRESH_STATE["status"] = "error"
        REFRESH_STATE["last_error"] = "market.db not found"
        return

    cmd = [
        sys.executable,
        script_path,
        "--base", base_file,
        "--db", db_file,
        "--use-tushare", "1"
    ]
    
    # Inject Tushare token from settings into subprocess environment
    env = os.environ.copy()
    try:
        from web.backend.api.settings import get_setting_value
        token = get_setting_value('tushare_token', '')
        if token:
            env["TUSHARE_TOKEN"] = token
    except Exception:
        pass
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT, env=env)
        
        if result.returncode == 0:
            REFRESH_STATE["status"] = "idle"
            REFRESH_STATE["last_run"] = sh_now().strftime("%H:%M:%S")
            logger.info("Market Refresh Success:", result.stdout)
        else:
            REFRESH_STATE["status"] = "error"
            REFRESH_STATE["last_error"] = result.stderr
            logger.error("Market Refresh Failed:", result.stderr)
            
    except Exception as e:
        REFRESH_STATE["status"] = "error"
        REFRESH_STATE["last_error"] = str(e)
        logger.error("Market Refresh Exception:", str(e))

# ETL State (separate from refresh)
ETL_STATE = {
    "status": "idle",   # idle | running | error
    "mode": "",         # full | fill_missing | retry_failed
    "last_run": None,
    "last_error": None,
    "progress": "",
    "last_stdout": "",
    "last_stderr": "",
}

import threading
from collections import deque

def _etl_tail(dq: deque, line: str, maxlines: int = 12) -> None:
    dq.append(line.rstrip("\n"))
    while len(dq) > maxlines:
        dq.popleft()


def _build_fill_missing_base(base_path: str):
    """
    构建"补齐缺失"临时 base CSV。
    返回三元组 (ok: bool, path_or_none: str | None, error_msg: str | None)：
      - (True,  path,  None)  → 有 actionable 股票，路径已写好
      - (True,  None,  None)  → 无 actionable 股票，正常静默结束
      - (False, None,  msg)   → 环境故障（CSV 缺失/market.db 未初始化），应报错
    """
    import csv as _csv
    import tempfile
    from datetime import datetime, timedelta
    from web.backend.database import get_market_db

    # 1. load universe
    universe_rows: list = []
    seen_codes: set = set()
    try:
        with open(base_path, encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                code = (row.get("ts_code") or "").strip()
                if code and not code.endswith(".BJ") and code not in seen_codes:
                    universe_rows.append(row)
                    seen_codes.add(code)
    except Exception as e:
        return (False, None, f"无法读取 stock_industry_base.csv：{e}")

    if not universe_rows:
        return (False, None, "stock_industry_base.csv 为空或格式错误，无法确定目标 universe")

    # 2. query DB
    updated_set: set = set()
    max_dates: dict = {}
    latest_date: str | None = None

    with get_market_db() as conn:
        if conn:
            row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
            latest_date = row[0] if row else None
            if latest_date:
                updated_set = {
                    r[0] for r in conn.execute(
                        "SELECT DISTINCT ts_code FROM daily_price WHERE trade_date = ?",
                        [latest_date]
                    ).fetchall()
                }
                max_dates = dict(conn.execute(
                    "SELECT ts_code, MAX(trade_date) FROM daily_price GROUP BY ts_code"
                ).fetchall())

    if not latest_date:
        return (False, None, "market.db 中 daily_price 无数据，请先执行全量入库")

    # 3. classify
    universe_codes = {r["ts_code"] for r in universe_rows}
    not_covered = universe_codes - updated_set
    never_seen = {c for c in not_covered if c not in max_dates}
    stale = not_covered - never_seen

    try:
        latest_dt = datetime.strptime(latest_date, "%Y-%m-%d")
        threshold = (latest_dt - timedelta(days=10)).strftime("%Y-%m-%d")
        recently_stale = {c for c in stale if max_dates.get(c, "") >= threshold}
    except Exception:
        recently_stale = set(stale)

    actionable = never_seen | recently_stale
    if not actionable:
        return (True, None, None)  # 正常：没有缺口

    # 4. write temp CSV
    actionable_rows = [r for r in universe_rows if r["ts_code"] in actionable]
    fieldnames = list(universe_rows[0].keys())
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False,
        encoding="utf-8", prefix="etl_fill_"
    )
    writer = _csv.DictWriter(tmp, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(actionable_rows)
    tmp.close()

    ETL_STATE["progress"] = (
        f"补齐模式：待补 {len(actionable)} 只"
        f"（{len(never_seen)} 只从未入库 + {len(recently_stale)} 只近期落后）"
    )
    logger.info(f"fill_missing base CSV: {tmp.name}, actionable={len(actionable)}")
    return (True, tmp.name, None)


def run_etl_market_script(mode: str = "full"):
    global ETL_STATE
    ETL_STATE["status"]  = "running"
    ETL_STATE["mode"]    = mode
    ETL_STATE["last_error"] = None
    ETL_STATE["progress"]   = "正在准备..."
    ETL_STATE["last_stdout"] = ""
    ETL_STATE["last_stderr"] = ""

    # 带超时等待写锁（避免与 drip 冲突，最多等 5 分钟）
    _mdb_lock = None
    _lock_acquired = False
    try:
        from web.backend.drip_sync import market_db_write_lock, maybe_resume_drip_after_manual_etl
        _mdb_lock = market_db_write_lock
    except Exception:
        maybe_resume_drip_after_manual_etl = lambda: None

    if _mdb_lock is not None:
        _lock_acquired = _mdb_lock.acquire(timeout=300)
        if not _lock_acquired:
            ETL_STATE["status"] = "idle"
            ETL_STATE["last_error"] = "无法获取写锁（等待超时 5 分钟），drip 可能正在运行"
            try:
                maybe_resume_drip_after_manual_etl()
            except Exception:
                pass
            return

    script_path = os.path.join(PROJECT_ROOT, "scripts", "etl", "get_market_data_db_robust.py")
    db_path     = MARKET_DB_PATH
    out_path    = os.path.join(PROJECT_ROOT, "market_enriched.csv")
    base_path   = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")

    def _release_lock_and_resume():
        if _lock_acquired and _mdb_lock is not None:
            try:
                _mdb_lock.release()
            except Exception:
                pass
        try:
            maybe_resume_drip_after_manual_etl()
        except Exception:
            pass

    if not os.path.exists(script_path):
        ETL_STATE["status"]     = "error"
        ETL_STATE["last_error"] = f"ETL script not found: {script_path}"
        _release_lock_and_resume()
        return

    temp_base: str | None = None
    actual_base = base_path

    if mode == "fill_missing":
        ok, temp_path, err_msg = _build_fill_missing_base(base_path)
        if not ok:
            # 环境故障：CSV 缺失/market.db 未初始化
            ETL_STATE["status"]     = "error"
            ETL_STATE["last_error"] = err_msg
            ETL_STATE["progress"]   = ""
            ETL_STATE["mode"]       = ""
            logger.error(f"fill_missing: {err_msg}")
            _release_lock_and_resume()
            return
        if temp_path is None:
            # 正常情况：没有 actionable 缺口，静默结束
            ETL_STATE["status"]   = "idle"
            ETL_STATE["progress"] = "补齐缺失：当前无缺口，无需入库"
            ETL_STATE["mode"]     = ""
            logger.info("fill_missing: no actionable stocks, skipping ETL")
            _release_lock_and_resume()
            return
        temp_base = temp_path
        actual_base = temp_base

    cmd = [
        sys.executable, script_path,
        "--base", actual_base,
        "--end-target", "today",
        "--skip-spot", "1",
        "--order", "tx,sina,ts,em",
        "--db", db_path,
        "--out", out_path,
    ]
    if mode == "retry_failed":
        cmd += ["--retry-failed-only", "1"]

    env = os.environ.copy()
    try:
        from web.backend.api.settings import get_setting_value
        token = get_setting_value("tushare_token", "")
        if token:
            env["TUSHARE_TOKEN"] = token
    except Exception:
        pass

    stdout_dq: deque = deque()
    stderr_dq: deque = deque()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=PROJECT_ROOT, env=env,
        )

        def _read_stdout():
            for line in proc.stdout:
                _etl_tail(stdout_dq, line)
                ETL_STATE["last_stdout"] = "\n".join(stdout_dq)
                stripped = line.strip()
                if stripped:
                    ETL_STATE["progress"] = stripped[-120:]

        def _read_stderr():
            for line in proc.stderr:
                _etl_tail(stderr_dq, line)
                ETL_STATE["last_stderr"] = "\n".join(stderr_dq)

        t_out = threading.Thread(target=_read_stdout, daemon=True)
        t_err = threading.Thread(target=_read_stderr, daemon=True)
        t_out.start()
        t_err.start()

        try:
            proc.wait(timeout=3600)   # 1-hour hard cap
        except subprocess.TimeoutExpired:
            proc.kill()
            t_out.join(timeout=2)
            t_err.join(timeout=2)
            ETL_STATE["status"]     = "error"
            ETL_STATE["last_error"] = "ETL 超时（> 60 分钟）"
            ETL_STATE["progress"]   = ""
            return

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        if proc.returncode == 0:
            ETL_STATE["status"] = "idle"
            ETL_STATE["progress"] = ""
            ETL_STATE["last_run"] = sh_now().strftime("%H:%M:%S")
            logger.info(f"ETL success (mode={mode})")
        else:
            ETL_STATE["status"]     = "error"
            ETL_STATE["last_error"] = (
                ETL_STATE["last_stderr"] or ETL_STATE["last_stdout"] or "Unknown error"
            )
            ETL_STATE["progress"] = ""
            logger.error(f"ETL failed (mode={mode}): {ETL_STATE['last_error'][:200]}")

    except Exception as e:
        ETL_STATE["status"]     = "error"
        ETL_STATE["last_error"] = str(e)
        ETL_STATE["progress"]   = ""
        logger.error(f"ETL exception (mode={mode}): {e}")
    finally:
        if _lock_acquired and _mdb_lock is not None:
            try:
                _mdb_lock.release()
            except Exception:
                pass
        try:
            maybe_resume_drip_after_manual_etl()
        except Exception:
            pass
        if temp_base and os.path.exists(temp_base):
            try:
                os.remove(temp_base)
            except Exception:
                pass

@router.post("/system/refresh-market")
async def refresh_market(background_tasks: BackgroundTasks):
    if REFRESH_STATE["status"] == "running":
        return {"status": "warning", "message": "Refresh already in progress"}
    
    background_tasks.add_task(run_market_refresh_script)
    return {"status": "success", "message": "Market data refresh started in background"}

@router.post("/system/etl-market")
async def etl_market(background_tasks: BackgroundTasks, mode: str = "full"):
    if ETL_STATE["status"] in ("running", "pending"):
        return {"status": "warning", "message": "ETL already in progress", "current_mode": ETL_STATE.get("mode", "")}
    # 与估值入库互斥，两个脚本都写 market.db
    if VALUATION_ETL_STATE["status"] in ("running", "pending"):
        return {"status": "warning", "message": "估值入库正在进行中，请等待完成后再启动行情入库"}

    valid_modes = ("full", "fill_missing", "retry_failed")
    if mode not in valid_modes:
        return {"status": "error", "message": f"Invalid mode. Must be one of: {valid_modes}"}

    # 占位 + 暂停 drip（通过互斥后才执行）
    ETL_STATE["status"] = "pending"
    try:
        from web.backend.drip_sync import pause_drip_for_manual_etl
        pause_drip_for_manual_etl()
    except Exception:
        pass

    background_tasks.add_task(run_etl_market_script, mode)
    labels = {"full": "全量入库", "fill_missing": "补齐缺失", "retry_failed": "重试失败记录"}
    return {"status": "success", "message": f"{labels[mode]}已启动（后台运行）", "mode": mode}

@router.get("/system/status")
async def get_system_status():
    status = REFRESH_STATE.copy()
    status["etl"] = ETL_STATE.copy()
    
    # Try to read the actual data date from the CSV (use csv module, NOT pandas)
    csv_path = os.path.join(PROJECT_ROOT, "market_enriched.csv")
    if os.path.exists(csv_path):
        try:
            import csv
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader)
                if "snapshot_trade_date" in header:
                    idx = header.index("snapshot_trade_date")
                    first_row = next(reader, None)
                    if first_row and len(first_row) > idx:
                        status["data_date"] = first_row[idx]
        except Exception as e:
            status["data_date_error"] = str(e)
    
    # Get market.db latest date
    from web.backend.database import get_market_db_rw
    try:
        with get_market_db_rw() as conn:
            if conn:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_price_trade_date ON daily_price(trade_date)")
                row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
                if row and row[0]:
                    status["db_date"] = str(row[0])
    except Exception:
        pass
            
    return status

@router.get("/system/scheduler-config")
async def get_scheduler_config():
    """Get current scheduler configuration"""
    from web.backend.scheduler import get_schedule_config
    config = get_schedule_config()
    return {"status": "success", "data": config}

@router.put("/system/scheduler-config")
async def update_scheduler_config(body: dict):
    """Update scheduler configuration and apply changes"""
    from web.backend.scheduler import save_schedule_config, apply_schedule, get_schedule_config
    
    new_config = body.get("config", {})
    if not new_config:
        return {"status": "error", "message": "No config provided"}
    
    # Validate
    current = get_schedule_config()
    for job_id, cfg in new_config.items():
        if job_id not in current:
            continue
        hour = cfg.get("hour", current[job_id]["hour"])
        minute = cfg.get("minute", current[job_id]["minute"])
        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            return {"status": "error", "message": f"Invalid time for {job_id}: {hour}:{minute}"}
        current[job_id]["hour"] = hour
        current[job_id]["minute"] = minute
        if "enabled" in cfg:
            current[job_id]["enabled"] = bool(cfg["enabled"])
    
    if save_schedule_config(current):
        apply_schedule()
        return {"status": "success", "message": "定时任务配置已更新", "data": current}
    else:
        return {"status": "error", "message": "保存配置失败"}

@router.get("/system/backup")
def backup_system_data():
    """Create a backup of user_data.db and config/"""
    import zipfile
    import io
    from fastapi.responses import StreamingResponse
    
    # Create valid filename with timestamp
    timestamp = sh_compact_timestamp()
    filename = f"backup_{timestamp}.zip"
    
    # Create in-memory zip
    mem_zip = io.BytesIO()
    
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Add user_data.db (stored under data/ to keep WAL sidecars in the mounted volume)
        db_path = USER_DB_PATH
        if os.path.exists(db_path):
            # B2: 用 SQLite backup API 生成一致性快照再打包。
            # WAL 模式下 checkpoint 可能被读事务阻塞（返回 busy），直接复制主库文件
            # 会静默遗漏 WAL 中已提交的数据；backup API 则保证快照是某个提交点的
            # 完整一致副本，不受 busy 状态影响，也无需事先 checkpoint。
            import sqlite3
            import tempfile
            fd, tmp_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                src = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
                try:
                    dst = sqlite3.connect(tmp_path, timeout=15)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
                zf.write(tmp_path, "data/user_data.db")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            
        # Add config folder
        config_dir = os.path.join(PROJECT_ROOT, "config")
        if os.path.exists(config_dir):
            for root, dirs, files in os.walk(config_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, PROJECT_ROOT)
                    zf.write(file_path, arcname)
    
    mem_zip.seek(0)
    
    return StreamingResponse(
        mem_zip, 
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.post("/system/restore")
async def restore_system_data(file: UploadFile):
    """Restore system data from backup zip"""
    import zipfile
    import shutil
    
    if not file.filename.endswith(".zip"):
        return {"status": "error", "message": "Invalid file type. Please upload a .zip file."}

    # W5: 旧路径兼容模式下拒绝恢复。启动时 USER_DB_PATH 若仍指向根目录旧库，
    # 恢复却会写入 data/user_data.db，当前进程继续读写旧库、重启后才切换，
    # 新旧数据分叉。先迁移 DB 到 data/ 并重启，再做恢复。
    _canonical_user_db = os.path.abspath(os.path.join(PROJECT_ROOT, "data", "user_data.db"))
    if os.path.abspath(USER_DB_PATH) != _canonical_user_db:
        return {"status": "error",
                "message": "检测到数据库仍在旧位置（项目根目录）。请先将其移动到 data/ 目录并重启后端，再恢复备份，避免新旧库数据分叉。"}

    try:
        # Read uploaded zip
        content = await file.read()
        import io
        import tempfile
        from web.backend.database import pending_restore_path, validate_user_db
        zip_file = io.BytesIO(content)

        # Verify zip
        if not zipfile.is_zipfile(zip_file):
             return {"status": "error", "message": "Invalid zip file."}

        # Extract
        with zipfile.ZipFile(zip_file, 'r') as zf:
            # W4: 保留 (zip 内原始名, 映射后目标名)：读取必须用原始名
            #（旧备份包里是根目录的 user_data.db），落盘才用映射名。
            pairs = _collect_restore_pairs(zf)

            if not pairs:
                return {"status": "warning", "message": "No valid backup files found in zip (user_data.db or config/)"}

            # 阶段 1：整包解压到临时文件并全部校验；任何失败都不改动现有文件
            staged = []      # [(tmp_path, target_path, is_db)]
            replaced = []    # [(target_path, backup_path or None)]
            kept_backups = []  # 回滚失败、需保留的 [(target_path, backup_path)]
            try:
                for original_name, target_name in pairs:
                    target_path = _resolve_restore_target(target_name)
                    if target_path.exists() and not target_path.is_file():
                        raise ValueError(f"恢复目标已存在且不是普通文件：{target_name}")
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp_path = tempfile.mkstemp(suffix=".restore-tmp", dir=str(target_path.parent))
                    os.close(fd)
                    staged.append((tmp_path, target_path, target_name == "data/user_data.db"))
                    with zf.open(original_name) as src, open(tmp_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    if target_name == "data/user_data.db":
                        validate_user_db(tmp_path)

                # 阶段 2：替换配置文件（保留原文件以便回滚）
                for tmp_path, target_path, is_db in staged:
                    if is_db:
                        continue
                    backup_path = None
                    if target_path.exists():
                        fd, backup_path = tempfile.mkstemp(suffix=".restore-bak", dir=str(target_path.parent))
                        os.close(fd)
                        try:
                            os.replace(target_path, backup_path)
                        except OSError:
                            os.unlink(backup_path)
                            raise
                    replaced.append((target_path, backup_path))
                    os.replace(tmp_path, target_path)

                # 阶段 3：最后才发布数据库暂存文件。Startup applies it via SQLite
                # before background services start; never replace a live DB
                # or remove its WAL/SHM while existing connections are open.
                for tmp_path, _, is_db in staged:
                    if is_db:
                        os.replace(tmp_path, pending_restore_path(USER_DB_PATH))
            except Exception as restore_error:
                # 回滚已替换的配置；已有的待恢复快照不受影响（仅成功时才被覆盖）
                for target_path, backup_path in reversed(replaced):
                    try:
                        if backup_path:
                            os.replace(backup_path, target_path)
                        elif target_path.exists():
                            target_path.unlink()
                    except OSError:
                        if backup_path:
                            # 回滚失败的备份是原配置的唯一副本，必须保留供人工恢复
                            kept_backups.append((target_path, backup_path))
                        logger.exception("恢复回滚失败：%s（原配置备份：%s）", target_path, backup_path)
                if kept_backups:
                    hints = "；".join(f"{b} → {t}" for t, b in kept_backups)
                    raise RuntimeError(
                        f"{restore_error}。配置回滚失败，原配置备份已保留，请手动复制回原位置：{hints}"
                    ) from restore_error
                raise
            finally:
                # 提交点是发布 .restore-pending；此后的临时文件清理失败只记警告，
                # 不能把已生效的恢复报告为失败。回滚失败保留的备份不删除。
                kept = {b for _, b in kept_backups}
                leftovers = [t for t, _, _ in staged] + [b for _, b in replaced if b and b not in kept]
                for leftover in leftovers:
                    try:
                        if os.path.exists(leftover):
                            os.unlink(leftover)
                    except OSError:
                        logger.warning("恢复临时文件清理失败，可手动删除：%s", leftover, exc_info=True)
            restored_count = len(staged)

        return {"status": "success",
                "message": f"已接收 {restored_count} 个备份文件。用户数据库仅暂存，重启后端后才会生效；重启前继续使用当前数据库。"}

    except Exception as e:
        return {"status": "error", "message": f"Restore failed: {str(e)}"}


def _collect_restore_pairs(zf):
    """收集 [(zip 内原始成员名, 映射后目标名)]。

    读取 zip 内容必须用原始成员名（旧备份包里 user_data.db 在根目录），
    而落盘目标用映射后的名字（统一为 data/user_data.db）。
    """
    pairs = []
    targets = set()
    for info in zf.infolist():
        if info.is_dir():
            continue
        target_name = _normalize_restore_member_name(info.filename)
        if target_name is None:
            continue
        if target_name in targets:
            raise ValueError(f"备份包含重复恢复目标：{target_name}")
        targets.add(target_name)
        pairs.append((info.filename, target_name))
    return pairs


def _normalize_restore_member_name(name: str) -> str | None:
    """只允许恢复 data/user_data.db 和 config/ 下的普通文件。

    兼容旧版备份包中位于 zip 根目录的 ``user_data.db``，统一映射到新位置。
    """
    normalized = PurePosixPath(name)

    if not name or normalized.is_absolute():
        return None

    parts = normalized.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None

    normalized_name = normalized.as_posix()
    if normalized_name in {"data/user_data.db", "user_data.db"}:
        return "data/user_data.db"
    if parts[0] == "config" and len(parts) > 1:
        return normalized_name
    return None


def _resolve_restore_target(member_name: str) -> Path:
    project_root = Path(PROJECT_ROOT).resolve()
    target_path = (project_root / member_name).resolve()
    if project_root not in target_path.parents and target_path != project_root / "data" / "user_data.db":
        raise ValueError(f"Unsafe restore target: {member_name}")
    return target_path


def _normalize_hk_sync_args(start: str | None, codes: list[str] | None) -> tuple[str | None, list[str] | None]:
    normalized_start = (start or "").strip() or None
    if normalized_start and not _YYYYMMDD_RE.fullmatch(normalized_start):
        raise ValueError("start 必须为 YYYYMMDD 格式")

    if not codes:
        return normalized_start, None

    normalized_codes: list[str] = []
    for raw_code in codes:
        code = str(raw_code or "").strip().upper()
        if not code:
            continue
        if not _HK_CODE_RE.fullmatch(code):
            raise ValueError("codes 必须为 5 位港股代码，格式如 09988.HK")
        normalized_codes.append(code)

    normalized_codes = list(dict.fromkeys(normalized_codes))
    if len(normalized_codes) > 200:
        raise ValueError("codes 最多允许 200 只股票")
    return normalized_start, normalized_codes or None


@router.get("/system/data-health")
def get_data_health():
    """Comprehensive data health check — reports freshness and counts for all key data sources"""
    import sqlite3
    from datetime import datetime
    from web.backend.time_utils import SH_TZ

    health = {}

    # 1. market.db
    market_db = MARKET_DB_PATH
    if os.path.exists(market_db):
        sz = round(os.path.getsize(market_db) / 1024 / 1024, 1)
        from web.backend.database import get_market_db
        with get_market_db() as conn:
            if conn:
                try:
                    dp_count = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
                    dp_latest = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]
                    dp_earliest = conn.execute("SELECT MIN(trade_date) FROM daily_price").fetchone()[0]
                    dp_stocks = conn.execute("SELECT COUNT(DISTINCT ts_code) FROM daily_price").fetchone()[0]
                except Exception:
                    dp_count, dp_latest, dp_earliest, dp_stocks = 0, None, None, 0
                try:
                    ds_count = conn.execute("SELECT COUNT(*) FROM daily_snapshot").fetchone()[0]
                    ds_latest = conn.execute("SELECT MAX(trade_date) FROM daily_snapshot").fetchone()[0]
                except Exception:
                    ds_count, ds_latest = 0, None
            else:
                dp_count, dp_latest, dp_earliest, dp_stocks = 0, None, None, 0
                ds_count, ds_latest = 0, None
        health["market_db"] = {
            "exists": True, "size_mb": sz,
            "daily_price_rows": dp_count,
            "daily_price_earliest": dp_earliest,
            "daily_price_latest": dp_latest,
            "daily_price_stocks": dp_stocks,
            "daily_snapshot_rows": ds_count, "daily_snapshot_latest": ds_latest,
        }
    else:
        health["market_db"] = {"exists": False}

    # 2. user_data.db
    from web.backend.database import get_db
    user_db = USER_DB_PATH
    if os.path.exists(user_db):
        sz = round(os.path.getsize(user_db) / 1024 / 1024, 1)
        with get_db() as conn:
            tables = {}
            for tbl in ["accounts", "holdings", "transactions", "capital_flows", "stock_pool", "risk_flags", "asset_history"]:
                try:
                    cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                    tables[tbl] = cnt
                except Exception:
                    tables[tbl] = -1  # table missing
        health["user_data_db"] = {"exists": True, "size_mb": sz, "tables": tables}
    else:
        health["user_data_db"] = {"exists": False}

    # 3. CSV files
    csvs = {
        "stock_industry_base.csv": "stock_industry_base.csv",
        "market_enriched.csv": "market_enriched.csv",
    }
    csv_health = {}
    for label, fname in csvs.items():
        path = os.path.join(PROJECT_ROOT, fname)
        if os.path.exists(path):
            sz = round(os.path.getsize(path) / 1024, 1)
            mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=SH_TZ).strftime("%Y-%m-%d %H:%M")
            csv_health[label] = {"exists": True, "size_kb": sz, "modified": mtime}
        else:
            csv_health[label] = {"exists": False}
    health["csv_files"] = csv_health

    # 4. Scheduler jobs
    health["scheduler"] = {
        "jobs": ["market_update(17:00)", "asset_snapshot(17:15)", "scoring(17:30)", "monthly_sync(18:00)"]
    }

    # 5. Log files
    log_dir = os.path.join(PROJECT_ROOT, "logs")
    if os.path.exists(log_dir):
        logs = sorted([f for f in os.listdir(log_dir) if f.endswith(".log")], reverse=True)[:5]
        health["recent_logs"] = logs
    else:
        health["recent_logs"] = []

    # 6. ETL live status (so frontend can show progress without a separate poll)
    health["etl_status"] = ETL_STATE.get("status", "idle")
    health["etl_progress"] = ETL_STATE.get("progress", "")
    health["etl_last_run"] = ETL_STATE.get("last_run")
    health["etl_last_error"] = ETL_STATE.get("last_error")

    return {"status": "success", "data": health}


@router.get("/system/market-coverage")
def get_market_coverage():
    """覆盖诊断：以 stock_industry_base.csv 为目标 universe，统计最新日覆盖情况"""
    import csv as _csv
    from datetime import datetime, timedelta

    # 健康标志（前端可据此区分"数据异常"和"正常零覆盖"）
    base_csv_ok = False
    market_db_ok = False
    latest_date_available = False
    diagnostic_error: str | None = None

    # 1. load target universe
    base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    universe_codes: set = set()
    if not os.path.exists(base_path):
        diagnostic_error = f"stock_industry_base.csv 不存在：{base_path}"
    else:
        try:
            with open(base_path, encoding="utf-8-sig") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    code = (row.get("ts_code") or "").strip()
                    if code and not code.endswith(".BJ"):
                        universe_codes.add(code)
            if universe_codes:
                base_csv_ok = True
            else:
                diagnostic_error = "stock_industry_base.csv 为空或格式错误"
        except Exception as e:
            diagnostic_error = f"读取 stock_industry_base.csv 失败：{e}"

    expected_count = len(universe_codes)

    # 2. query daily_price
    from web.backend.database import get_market_db
    latest_date: str | None = None
    updated_set: set = set()
    max_dates: dict = {}

    try:
        with get_market_db() as conn:
            if conn:
                market_db_ok = True
                row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
                latest_date = row[0] if row else None
                if latest_date:
                    latest_date_available = True
                    updated_set = {
                        r[0] for r in conn.execute(
                            "SELECT DISTINCT ts_code FROM daily_price WHERE trade_date = ?",
                            [latest_date]
                        ).fetchall()
                    }
                    max_dates = dict(conn.execute(
                        "SELECT ts_code, MAX(trade_date) FROM daily_price GROUP BY ts_code"
                    ).fetchall())
            else:
                if diagnostic_error is None:
                    diagnostic_error = "market.db 无法连接"
    except Exception as e:
        if diagnostic_error is None:
            diagnostic_error = f"查询 market.db 失败：{e}"

    # 3. compute coverage
    not_covered = universe_codes - updated_set
    never_seen = {c for c in not_covered if c not in max_dates}
    stale = not_covered - never_seen

    recently_stale: set = set()
    if latest_date:
        try:
            latest_dt = datetime.strptime(latest_date, "%Y-%m-%d")
            threshold = (latest_dt - timedelta(days=10)).strftime("%Y-%m-%d")
            recently_stale = {c for c in stale if max_dates.get(c, "") >= threshold}
        except Exception:
            recently_stale = set(stale)

    actionable = never_seen | recently_stale
    coverage_pct = round(len(updated_set) / expected_count * 100, 1) if expected_count > 0 else 0.0

    # 4. failed_hist count
    failed_hist_count = 0
    failed_hist_path = os.path.join(PROJECT_ROOT, "logs", "failed_hist.csv")
    if os.path.exists(failed_hist_path):
        try:
            with open(failed_hist_path) as f:
                failed_hist_count = max(0, sum(1 for _ in f) - 1)  # subtract header
        except Exception:
            pass

    return {
        "status": "success",
        "data": {
            # 健康标志
            "base_csv_ok": base_csv_ok,
            "market_db_ok": market_db_ok,
            "latest_date_available": latest_date_available,
            "diagnostic_error": diagnostic_error,
            # 统计数据
            "latest_date": latest_date,
            "expected_count": expected_count,
            "updated_count": len(updated_set),
            "coverage_pct": coverage_pct,
            "not_covered_count": len(not_covered),
            "actionable_count": len(actionable),
            "never_seen_count": len(never_seen),
            "recently_stale_count": len(recently_stale),
            "long_stale_count": len(stale) - len(recently_stale),
            "failed_hist_count": failed_hist_count,
            "not_covered_samples": sorted(list(not_covered))[:20],
        }
    }


@router.get("/system/logs/{filename}")
def read_log_file(filename: str, tail: int = 100):
    """Read tail of a log file"""
    # Security: Only allow reading from logs/ directory
    if ".." in filename or "/" in filename or "\\" in filename:
        return {"status": "error", "message": "Invalid filename"}

    log_path = os.path.join(PROJECT_ROOT, "logs", filename)
    if not os.path.exists(log_path):
        return {"status": "error", "message": "Log file not found"}

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return {
            "status": "success",
            "filename": filename,
            "total_lines": len(lines),
            "lines": lines[-tail:]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Industry Sync via pytdx3 ──────────────────────────────────

# Data directory for uploaded mapping files
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

SYNC_INDUSTRY_STATE = {
    "status": "idle",   # idle, running, error
    "last_run": None,
    "last_error": None,
    "result": None,
}


def _get_pinyin_initials(name: str) -> str:
    """获取中文名称的拼音首字母（大写）。需要 pypinyin 库。"""
    try:
        from pypinyin import lazy_pinyin, Style
        initials = lazy_pinyin(name, style=Style.FIRST_LETTER, errors="ignore")
        return "".join(initials).upper()
    except ImportError:
        return ""


def _save_industry_csv_to_db(csv_path: str, synced_at: str) -> int:
    """把 stock_industry_base.csv 的内容写入 user_data.db 的 stock_industry 表。
    每次调用会完整替换（DELETE + INSERT）。包含拼音首字母字段用于搜索。"""
    import csv as csv_module
    from web.backend.database import get_db

    if not os.path.exists(csv_path):
        return 0

    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv_module.DictReader(f):
            name = row.get("name", "")
            rows.append((
                row.get("ts_code", ""),
                name,
                row.get("tdx_l1", ""),
                row.get("tdx_l2", ""),
                row.get("tdx_l3", ""),
                row.get("anchor_industry", ""),
                row.get("list_date", ""),
                synced_at,
                _get_pinyin_initials(name),
            ))

    with get_db() as conn:
        conn.execute("DELETE FROM stock_industry")
        conn.executemany(
            "INSERT INTO stock_industry "
            "(ts_code, name, tdx_l1, tdx_l2, tdx_l3, anchor_industry, list_date, synced_at, name_initials) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

    return len(rows)


def run_sync_industry_script():
    """Background task: run sync_industry_pytdx3.py"""
    global SYNC_INDUSTRY_STATE
    SYNC_INDUSTRY_STATE["status"] = "running"
    SYNC_INDUSTRY_STATE["last_error"] = None
    SYNC_INDUSTRY_STATE["result"] = None

    script_path = os.path.join(PROJECT_ROOT, "scripts", "etl", "sync_industry_pytdx3.py")
    csv_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
    yaml_path = os.path.join(PROJECT_ROOT, "config", "scoring.yaml")
    incon_path = os.path.join(DATA_DIR, "incon.dat")
    log_file = os.path.join(PROJECT_ROOT, "logs", "sync_industry.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    cmd = [
        sys.executable,
        script_path,
        "--out", csv_path,
        "--skip-bj", "1",
    ]
    # Add yaml if exists
    if os.path.exists(yaml_path):
        cmd.extend(["--yaml", yaml_path])
    # Add incon.dat if exists
    if os.path.exists(incon_path):
        cmd.extend(["--incon", incon_path])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=PROJECT_ROOT, timeout=120,
        )
        if result.returncode == 0:
            SYNC_INDUSTRY_STATE["status"] = "idle"
            from datetime import datetime
            now_str = sh_timestamp_str()
            SYNC_INDUSTRY_STATE["last_run"] = now_str
            # Parse result from stdout
            stdout = result.stdout or ""
            SYNC_INDUSTRY_STATE["result"] = stdout[-500:]
            logger.info("Industry sync success: %s", stdout[-200:])

            # Save CSV to DB for persistence
            try:
                n = _save_industry_csv_to_db(csv_path, now_str)
                logger.info("Industry data saved to DB: %d rows", n)
                SYNC_INDUSTRY_STATE["result"] = (stdout[-400:] + f"\n[DB] 已保存 {n} 条到数据库")
            except Exception as e:
                logger.warning("Failed to save industry to DB: %s", e)

            # Reload market.py stock cache
            try:
                from web.backend.api.market import load_stock_cache
                load_stock_cache()
                logger.info("Stock cache reloaded after industry sync")
            except Exception as e:
                logger.warning("Failed to reload stock cache: %s", e)
        else:
            SYNC_INDUSTRY_STATE["status"] = "error"
            SYNC_INDUSTRY_STATE["last_error"] = (result.stderr or "")[-300:]
            logger.error("Industry sync failed: %s", result.stderr)

        # Write log
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"=== stdout ===\n{result.stdout}\n\n=== stderr ===\n{result.stderr}\n")

    except subprocess.TimeoutExpired:
        SYNC_INDUSTRY_STATE["status"] = "error"
        SYNC_INDUSTRY_STATE["last_error"] = "同步超时 (>120s)"
    except Exception as e:
        SYNC_INDUSTRY_STATE["status"] = "error"
        SYNC_INDUSTRY_STATE["last_error"] = str(e)
        logger.error("Industry sync exception: %s", e)


@router.post("/system/sync-industry")
async def sync_industry(background_tasks: BackgroundTasks):
    """Trigger industry classification sync via pytdx3 (X前缀 RSHY 体系)"""
    if SYNC_INDUSTRY_STATE["status"] == "running":
        return {"status": "warning", "message": "行业同步正在进行中"}

    background_tasks.add_task(run_sync_industry_script)
    return {"status": "success", "message": "行业分类同步已启动（后台运行）"}


@router.get("/system/sync-industry/status")
def get_sync_industry_status():
    """Get industry sync status"""
    return {"status": "success", "data": SYNC_INDUSTRY_STATE.copy()}


@router.post("/system/upload-industry-mapping")
async def upload_industry_mapping(file: UploadFile):
    """
    Upload incon.dat file for industry name mapping.
    This file is used by sync_industry_pytdx3.py to resolve
    industry codes (T01, T0101, etc.) to Chinese names.
    """
    import shutil

    if not file.filename:
        return {"status": "error", "message": "未选择文件"}

    # Accept .dat files or any file named incon*
    fname = file.filename.lower()
    if not (fname.endswith(".dat") or "incon" in fname):
        return {"status": "error", "message": "仅支持 .dat 格式的行业映射文件"}

    save_path = os.path.join(DATA_DIR, "incon.dat")
    try:
        content = await read_limited_upload(file, MAX_GENERIC_UPLOAD_BYTES)
        with open(save_path, "wb") as f:
            f.write(content)

        # Validate: try parsing #TDXRSHY (X-prefix, RSHY system only)
        try:
            sys.path.insert(0, PROJECT_ROOT)
            from scripts.etl.sync_industry_pytdx3 import parse_incon_tdxrshy
            l1r, l2r, l3r = parse_incon_tdxrshy(content)

            if l1r:
                return {
                    "status": "success",
                    "message": f"行业映射文件已上传: #TDXRSHY(X前缀) L1={len(l1r)} L2={len(l2r)} L3={len(l3r)}",
                    "has_rshy": True,
                }
            else:
                return {
                    "status": "warning",
                    "message": "文件已保存，但未能解析 #TDXRSHY 段，请确认文件来自通达信安装目录。",
                    "has_rshy": False,
                }
        except ImportError:
            return {
                "status": "success",
                "message": "行业映射文件已上传（pytdx3 未安装，跳过验证）",
            }
    except Exception as e:
        return {"status": "error", "message": f"上传失败: {str(e)}"}


@router.get("/system/industry")
def get_industry_list(
    page: int = 1,
    page_size: int = 100,
    search: str = "",
    l1: str = "",   # 筛选一级行业（大类）
    l2: str = "",   # 筛选二级行业（中类）
):
    """读取行业分类（优先从 DB stock_industry 表，不存在时 fallback 到 CSV）
    行业层级：tdx_l1=大类(X10级), tdx_l2=中类(X1001级), tdx_l3=细分(X100101级)
    """
    import pandas as pd
    from fastapi import HTTPException
    from web.backend.database import get_db

    try:
        # ── 1. 优先从 DB 读取 ──────────────────────────────
        with get_db() as conn:
            count_row = conn.execute("SELECT COUNT(*) FROM stock_industry").fetchone()
            db_count = count_row[0] if count_row else 0

        if db_count > 0:
            # ── 从 DB 查询（支持拼音首字母搜索）──────────
            # 判断是否为纯字母搜索（拼音首字母模式）
            is_pinyin_search = bool(search) and search.replace(" ", "").isalpha() and search.isascii()

            # 构建过滤条件
            conditions = []
            params: list = []

            if search:
                if is_pinyin_search:
                    conditions.append(
                        "(ts_code LIKE ? OR name LIKE ? OR UPPER(name_initials) LIKE UPPER(?))"
                    )
                    params += [f"%{search}%", f"%{search}%", f"%{search}%"]
                else:
                    conditions.append("(ts_code LIKE ? OR name LIKE ?)")
                    params += [f"%{search}%", f"%{search}%"]

            if l1:
                conditions.append("tdx_l1 = ?")
                params.append(l1.strip())
            if l2:
                conditions.append("tdx_l2 = ?")
                params.append(l2.strip())

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            with get_db() as conn:
                # 过滤后总数
                total_row = conn.execute(
                    f"SELECT COUNT(*) FROM stock_industry {where}", params
                ).fetchone()
                total = total_row[0] if total_row else 0

                # 分页数据
                offset = (page - 1) * page_size
                page_rows = conn.execute(
                    f"SELECT ts_code, name, tdx_l1, tdx_l2, tdx_l3, anchor_industry, list_date "
                    f"FROM stock_industry {where} ORDER BY ts_code LIMIT ? OFFSET ?",
                    params + [page_size, offset],
                ).fetchall()
                records = [dict(r) for r in page_rows]

                # 全量统计（不含搜索/行业过滤，用于分布面板）
                l1_rows = conn.execute(
                    "SELECT tdx_l1, COUNT(*) as cnt FROM stock_industry "
                    "WHERE tdx_l1 != '' GROUP BY tdx_l1"
                ).fetchall()
                l1_counts = {r["tdx_l1"]: r["cnt"] for r in l1_rows}

                l2_rows = conn.execute(
                    "SELECT tdx_l2, COUNT(*) as cnt FROM stock_industry "
                    "WHERE tdx_l2 != '' GROUP BY tdx_l2"
                ).fetchall()
                l2_counts = {r["tdx_l2"]: r["cnt"] for r in l2_rows}

                total_all_row = conn.execute("SELECT COUNT(*) FROM stock_industry").fetchone()
                total_all = total_all_row[0] if total_all_row else 0

            return {
                "status": "success",
                "data": records,
                "total": total,
                "total_all": total_all,
                "l1_counts": l1_counts,
                "l2_counts": l2_counts,
                "source": "db",
            }

        else:
            # ── 2. Fallback: 从 CSV 读取（不支持拼音搜索）──
            csv_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
            if not os.path.exists(csv_path):
                return {"status": "warning", "message": "行业分类数据不存在，请先同步", "data": [], "total": 0}
            df_all = pd.read_csv(csv_path, dtype=str).fillna("")
            df = df_all.copy()

            if search:
                mask = (df["ts_code"].str.contains(search, case=False, na=False) |
                        df.get("name", pd.Series(dtype=str)).str.contains(search, case=False, na=False))
                df = df[mask]
            if l1 and "tdx_l1" in df.columns:
                df = df[df["tdx_l1"].str.strip() == l1.strip()]
            if l2 and "tdx_l2" in df.columns:
                df = df[df["tdx_l2"].str.strip() == l2.strip()]

            total = len(df)
            total_all = len(df_all)
            l1_counts = df_all[df_all["tdx_l1"].str.strip() != ""]["tdx_l1"].value_counts().to_dict() if "tdx_l1" in df_all.columns else {}
            l2_counts = df_all[df_all["tdx_l2"].str.strip() != ""]["tdx_l2"].value_counts().to_dict() if "tdx_l2" in df_all.columns else {}

            start = (page - 1) * page_size
            records = df.iloc[start: start + page_size].to_dict(orient="records")
            return {
                "status": "success",
                "data": records,
                "total": total,
                "total_all": total_all,
                "l1_counts": l1_counts,
                "l2_counts": l2_counts,
                "source": "csv",
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Valuation ETL (PB/PE — build_fundamentals_and_valuation.py) ──────────────

VALUATION_ETL_STATE = {
    "status": "idle",   # idle | running | error
    "mode": "",         # holdings | full
    "progress": "",
    "last_error": "",
    "last_stdout": "",
    "last_stderr": "",
    "last_run": None,
}


def run_valuation_etl_script(mode: str = "holdings", ts_codes: list = None, force: bool = False):
    """后台运行 build_fundamentals_and_valuation.py，补充 daily_valuation PB/PE 数据。"""
    global VALUATION_ETL_STATE
    VALUATION_ETL_STATE["status"] = "running"
    VALUATION_ETL_STATE["mode"] = mode
    VALUATION_ETL_STATE["last_error"] = ""
    VALUATION_ETL_STATE["progress"] = "正在准备..."
    VALUATION_ETL_STATE["last_stdout"] = ""
    VALUATION_ETL_STATE["last_stderr"] = ""

    # 带超时等待写锁（避免与 drip 冲突，最多等 5 分钟）
    _val_mdb_lock = None
    _val_lock_acquired = False
    try:
        from web.backend.drip_sync import market_db_write_lock, maybe_resume_drip_after_manual_etl as _val_resume
        _val_mdb_lock = market_db_write_lock
    except Exception:
        _val_resume = lambda: None

    if _val_mdb_lock is not None:
        _val_lock_acquired = _val_mdb_lock.acquire(timeout=300)
        if not _val_lock_acquired:
            VALUATION_ETL_STATE["status"] = "idle"
            VALUATION_ETL_STATE["last_error"] = "无法获取写锁（等待超时 5 分钟），drip 可能正在运行"
            try:
                _val_resume()
            except Exception:
                pass
            return

    script_path = os.path.join(PROJECT_ROOT, "scripts", "etl", "build_fundamentals_and_valuation.py")
    db_path = MARKET_DB_PATH
    base_path = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")

    def _val_release_lock_and_resume():
        if _val_lock_acquired and _val_mdb_lock is not None:
            try:
                _val_mdb_lock.release()
            except Exception:
                pass
        try:
            _val_resume()
        except Exception:
            pass

    if not os.path.exists(script_path):
        VALUATION_ETL_STATE["status"] = "error"
        VALUATION_ETL_STATE["last_error"] = f"脚本不存在：{script_path}"
        _val_release_lock_and_resume()
        return

    import tempfile, time as _time
    tmp_out: str | None = None  # 需在 finally 里清理

    cmd = [sys.executable, script_path, "--db", db_path]

    if mode == "holdings" and ts_codes:
        # 用临时路径，避免覆盖全量 market_enriched.csv（holdings 只是子集）
        tmp_out = os.path.join(tempfile.gettempdir(), f"etl_val_{int(_time.time())}.csv")
        VALUATION_ETL_STATE["progress"] = f"补齐持仓估值：{len(ts_codes)} 只"
        cmd += ["--ts", ",".join(ts_codes), "--out", tmp_out]
    else:
        cmd += ["--base-file", base_path]

    env = os.environ.copy()
    token = ""
    try:
        from web.backend.api.settings import get_setting_value
        token = get_setting_value("tushare_token", "")
        etl_source = get_setting_value("etl_source", "akshare")
        if token:
            env["TUSHARE_TOKEN"] = token
        if etl_source == "tushare" and token:
            cmd += ["--source", "tushare"]
            # Token 通过环境变量 TUSHARE_TOKEN 传入（已在上方设置），不放进命令行参数
        elif etl_source == "tushare" and not token:
            logger.warning("etl_source=tushare 但未配置 Tushare Token，回退使用 akshare")
    except Exception:
        pass

    # holdings 模式始终需要抓取（目标就是缺失的股票），全量模式默认增量跳过已有数据
    if force or mode == "holdings":
        cmd += ["--force-refresh"]

    stdout_dq: deque = deque()
    stderr_dq: deque = deque()

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=PROJECT_ROOT, env=env,
        )

        def _read_stdout():
            for line in proc.stdout:
                _etl_tail(stdout_dq, line)
                VALUATION_ETL_STATE["last_stdout"] = "\n".join(stdout_dq)
                stripped = line.strip()
                if stripped:
                    VALUATION_ETL_STATE["progress"] = stripped[-120:]

        def _read_stderr():
            for line in proc.stderr:
                _etl_tail(stderr_dq, line)
                VALUATION_ETL_STATE["last_stderr"] = "\n".join(stderr_dq)

        t_out = threading.Thread(target=_read_stdout, daemon=True)
        t_err = threading.Thread(target=_read_stderr, daemon=True)
        t_out.start()
        t_err.start()

        try:
            proc.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            proc.kill()
            t_out.join(timeout=2)
            t_err.join(timeout=2)
            VALUATION_ETL_STATE["status"] = "error"
            VALUATION_ETL_STATE["last_error"] = "估值入库超时（> 120 分钟）"
            VALUATION_ETL_STATE["progress"] = ""
            return

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        if proc.returncode == 0:
            VALUATION_ETL_STATE["status"] = "idle"
            VALUATION_ETL_STATE["progress"] = ""
            VALUATION_ETL_STATE["last_run"] = sh_now().strftime("%H:%M:%S")
            logger.info(f"Valuation ETL success (mode={mode})")
        else:
            VALUATION_ETL_STATE["status"] = "error"
            VALUATION_ETL_STATE["last_error"] = (
                VALUATION_ETL_STATE["last_stderr"] or VALUATION_ETL_STATE["last_stdout"] or "未知错误"
            )
            VALUATION_ETL_STATE["progress"] = ""
            logger.error(f"Valuation ETL failed (mode={mode})")

    except Exception as e:
        VALUATION_ETL_STATE["status"] = "error"
        VALUATION_ETL_STATE["last_error"] = str(e)
        VALUATION_ETL_STATE["progress"] = ""
        logger.error(f"Valuation ETL exception: {e}")
    finally:
        if _val_lock_acquired and _val_mdb_lock is not None:
            try:
                _val_mdb_lock.release()
            except Exception:
                pass
        try:
            _val_resume()
        except Exception:
            pass
        # 清理 holdings 模式生成的临时导出文件
        if tmp_out and os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except Exception:
                pass


@router.get("/system/valuation-coverage")
def get_valuation_coverage():
    """检查当前持仓股票的 daily_valuation（PB/PE）覆盖情况。"""
    from datetime import datetime, timedelta
    from web.backend.database import get_db, get_market_db

    # 1. 获取持仓股票列表
    holding_codes: list = []
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ts_code FROM holdings WHERE quantity > 0"
            ).fetchall()
            holding_codes = [r[0] for r in rows]
    except Exception as e:
        return {"status": "error", "message": f"读取持仓失败：{e}"}

    if not holding_codes:
        return {"status": "success", "data": {
            "holding_count": 0, "covered": 0, "missing_count": 0,
            "missing": [], "latest_price_date": None,
        }}

    # 2. 检查 daily_valuation 覆盖（近 30 天内有无记录算"已覆盖"）
    latest_price_date: str | None = None
    covered_set: set = set()
    diagnostic_error: str | None = None
    try:
        with get_market_db() as conn:
            if conn:
                row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
                latest_price_date = row[0] if row else None
                if latest_price_date:
                    # daily_valuation.trade_date 格式为 "%Y-%m-%d"，cutoff 需一致
                    cutoff = (
                        datetime.strptime(latest_price_date, "%Y-%m-%d") - timedelta(days=30)
                    ).strftime("%Y-%m-%d")
                    placeholders = ",".join(["?"] * len(holding_codes))
                    rows = conn.execute(
                        f"SELECT DISTINCT ts_code FROM daily_valuation "
                        f"WHERE ts_code IN ({placeholders}) AND trade_date >= ?",
                        holding_codes + [cutoff],
                    ).fetchall()
                    covered_set = {r[0] for r in rows}

                    # 对 daily_valuation 缺失的股票，回退查 daily_snapshot（与持仓页口径一致）
                    dv_missing = [c for c in holding_codes if c not in covered_set]
                    if dv_missing:
                        mp = ",".join(["?"] * len(dv_missing))
                        snap_rows = conn.execute(
                            f"""SELECT DISTINCT ds.ts_code FROM daily_snapshot ds
                                WHERE ds.ts_code IN ({mp})
                                  AND ds.pb IS NOT NULL AND ds.pb != 0
                                          AND ds.pe_ttm IS NOT NULL AND ds.pe_ttm != 0
                                  AND ds.trade_date = (
                                      SELECT MAX(trade_date) FROM daily_snapshot
                                      WHERE ts_code = ds.ts_code
                                  )""",
                            dv_missing,
                        ).fetchall()
                        covered_set.update(r[0] for r in snap_rows)
            else:
                diagnostic_error = "market.db 无法连接"
    except Exception as e:
        diagnostic_error = f"查询 market.db 失败：{e}"

    missing = [c for c in holding_codes if c not in covered_set]
    return {"status": "success", "data": {
        "holding_count": len(holding_codes),
        "covered": len(holding_codes) - len(missing),
        "missing_count": len(missing),
        "missing": missing,
        "missing_samples": missing[:10],
        "latest_price_date": latest_price_date,
        "diagnostic_error": diagnostic_error,
    }}


@router.post("/system/etl-valuation")
async def etl_valuation(background_tasks: BackgroundTasks, mode: str = "holdings", force: bool = False):
    """触发估值 ETL（PB/PE 数据入库）。mode: holdings=仅补持仓缺失, full=全量。force=true 强制重抓已有数据"""
    if VALUATION_ETL_STATE["status"] in ("running", "pending"):
        return {
            "status": "warning",
            "message": "估值入库正在进行中",
            "current_mode": VALUATION_ETL_STATE.get("mode", ""),
        }
    # 与行情入库互斥，两个脚本都写 market.db
    if ETL_STATE["status"] in ("running", "pending"):
        return {"status": "warning", "message": "行情入库正在进行中，请等待完成后再启动估值入库"}

    if mode not in ("holdings", "full"):
        return {"status": "error", "message": "无效 mode，应为 holdings 或 full"}

    # 占位 + 暂停 drip
    VALUATION_ETL_STATE["status"] = "pending"
    try:
        from web.backend.drip_sync import pause_drip_for_manual_etl
        pause_drip_for_manual_etl()
    except Exception:
        pass

    ts_codes: list = []
    if mode == "holdings":
        from datetime import datetime, timedelta
        from web.backend.database import get_db, get_market_db
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT ts_code FROM holdings WHERE quantity > 0"
                ).fetchall()
                holding_codes = [r[0] for r in rows]
            if holding_codes:
                with get_market_db() as conn:
                    if conn:
                        row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
                        latest = row[0] if row else None
                        if latest:
                            # daily_valuation.trade_date 格式为 "%Y-%m-%d"，cutoff 需一致
                            cutoff = (
                                datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=30)
                            ).strftime("%Y-%m-%d")
                            placeholders = ",".join(["?"] * len(holding_codes))
                            rows = conn.execute(
                                f"SELECT DISTINCT ts_code FROM daily_valuation "
                                f"WHERE ts_code IN ({placeholders}) AND trade_date >= ?",
                                holding_codes + [cutoff],
                            ).fetchall()
                            covered = {r[0] for r in rows}
                            # 回退查 daily_snapshot（与持仓页口径一致）
                            dv_missing = [c for c in holding_codes if c not in covered]
                            if dv_missing:
                                mp = ",".join(["?"] * len(dv_missing))
                                snap_rows = conn.execute(
                                    f"""SELECT DISTINCT ds.ts_code FROM daily_snapshot ds
                                        WHERE ds.ts_code IN ({mp})
                                          AND ds.pb IS NOT NULL AND ds.pb != 0
                                          AND ds.pe_ttm IS NOT NULL AND ds.pe_ttm != 0
                                          AND ds.trade_date = (
                                              SELECT MAX(trade_date) FROM daily_snapshot
                                              WHERE ts_code = ds.ts_code
                                          )""",
                                    dv_missing,
                                ).fetchall()
                                covered.update(r[0] for r in snap_rows)
                            ts_codes = [c for c in holding_codes if c not in covered]
            if not ts_codes:
                return {"status": "success", "message": "持仓股票估值数据已完整，无需补充"}
        except Exception as e:
            return {"status": "error", "message": f"获取缺失持仓失败：{e}"}

    background_tasks.add_task(
        run_valuation_etl_script,
        mode,
        ts_codes if mode == "holdings" else None,
        force,
    )
    labels = {"holdings": "补齐持仓估值", "full": "全量估值入库"}
    return {
        "status": "success",
        "message": f"{labels[mode]}已启动（后台运行）",
        "mode": mode,
        "ts_count": len(ts_codes),
    }


@router.get("/system/valuation-etl-status")
def get_valuation_etl_status():
    """查询估值 ETL 进度"""
    return {"status": "success", "data": VALUATION_ETL_STATE.copy()}


@router.get("/system/incon-status")
def get_incon_status():
    """Check whether incon.dat has been uploaded"""
    from datetime import datetime
    from web.backend.time_utils import SH_TZ
    path = os.path.join(DATA_DIR, "incon.dat")
    if os.path.exists(path):
        sz = round(os.path.getsize(path) / 1024, 1)
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=SH_TZ).strftime("%Y-%m-%d %H:%M")
        return {"exists": True, "size_kb": sz, "modified": mtime}
    return {"exists": False}


@router.get("/system/pipeline-status")
def get_pipeline_status():
    """返回今日自动任务各步骤的执行状态"""
    # lazy import 避免循环导入（scheduler.py 在内部 import 了 system.py 的函数）
    from web.backend.scheduler import SCHEDULER_LAST_RUN, _scheduler

    key_jobs = [
        ("ipo_calendar_sync", "打新日历"),
        ("market_update",   "行情入库"),
        ("valuation_build", "估值构建"),
        ("factor_build",    "enriched重算"),
    ]
    steps = []
    for job_id, name in key_jobs:
        last = SCHEDULER_LAST_RUN.get(job_id, {})
        next_run = None
        try:
            job = _scheduler.get_job(job_id) if _scheduler else None
            if job and job.next_run_time:
                next_run = job.next_run_time.strftime("%H:%M")
        except Exception:
            pass
        steps.append({
            "id": job_id,
            "name": name,
            "last_run_time": last.get("time"),
            "last_run_status": last.get("status"),   # ok | error | skipped | None
            "last_run_detail": last.get("detail"),
            "next_run": next_run,
        })

    return {"status": "success", "data": {
        "steps": steps,
        "manual_etl_status": ETL_STATE.get("status"),
        "manual_etl_last_run": ETL_STATE.get("last_run"),
        "valuation_etl_status": VALUATION_ETL_STATE.get("status"),
        "valuation_etl_last_run": VALUATION_ETL_STATE.get("last_run"),
    }}


# ── Drip Sync API ─────────────────────────────────────────────────────────────

@router.get("/system/drip-sync/status")
def get_drip_sync_status():
    """Drip sync 运行状态 + 队列摘要"""
    try:
        from web.backend.drip_sync import DRIP_STATE, get_queue_summary
        summary = get_queue_summary()
        return {
            "status": "success",
            "data": {
                **DRIP_STATE,
                "queue_total": summary["total"],
                "queue_candidates": summary["candidates"],
                "queue_failed_stocks": summary["failed_stocks"],
            },
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/system/drip-sync/toggle")
def toggle_drip_sync(body: dict = {}):
    """启用或暂停 drip sync"""
    try:
        from web.backend.drip_sync import pause_drip, resume_drip, DRIP_STATE
        enabled = body.get("enabled", not DRIP_STATE["enabled"])
        if enabled:
            resume_drip()
            return {"status": "success", "message": "持续补数已启用", "enabled": True}
        else:
            pause_drip()
            return {"status": "success", "message": "持续补数已暂停", "enabled": False}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/system/drip-sync/reset-cycle")
def reset_drip_cycle():
    """重置 drip sync 轮次（清空 cycle_seq 和 fail_count）"""
    try:
        from web.backend.drip_sync import reset_cycle
        reset_cycle()
        return {"status": "success", "message": "轮次已重置，所有股票将重新纳入下一轮扫描"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── HK Price Sync API ─────────────────────────────────────────────────────────

HK_PRICE_STATE: dict = {
    "status": "idle",   # idle | running | done | error
    "last_run": None,
    "last_error": None,
    "progress": "",
    "rows_written": 0,
    "last_stdout": "",
}
_hk_price_lock = threading.Lock()


def _run_hk_price_sync(start: str | None, codes: list[str] | None) -> None:
    global HK_PRICE_STATE

    script = os.path.join(PROJECT_ROOT, "scripts", "etl", "sync_hk_daily_prices.py")
    user_db = USER_DB_PATH
    market_db = MARKET_DB_PATH

    if not os.path.isfile(market_db):
        HK_PRICE_STATE["status"] = "error"
        HK_PRICE_STATE["last_error"] = "market.db 不存在"
        return

    cmd = [sys.executable, script]
    if start:
        cmd += ["--start", start]
    cmd += ["--adjust", "qfq"]
    if codes:
        cmd += ["--codes"] + codes
    env = os.environ.copy()
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    env.setdefault("NO_PROXY", "*")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=PROJECT_ROOT, env=env,
            timeout=600,  # 10 分钟上限
        )
        stdout = (result.stdout or "")[-2000:]
        HK_PRICE_STATE["last_stdout"] = stdout
        if result.returncode == 0:
            # 从输出里提取写入行数
            rows = 0
            for line in result.stdout.splitlines():
                if "共写入" in line and "行" in line:
                    import re
                    m = re.search(r"(\d+)\s*行", line)
                    if m:
                        rows = int(m.group(1))
            HK_PRICE_STATE["rows_written"] = rows
            HK_PRICE_STATE["status"] = "done"
            HK_PRICE_STATE["progress"] = f"完成，写入 {rows} 行"
            from datetime import datetime
            HK_PRICE_STATE["last_run"] = sh_timestamp_str()
        else:
            HK_PRICE_STATE["status"] = "error"
            HK_PRICE_STATE["last_error"] = (result.stderr or result.stdout or "未知错误")[-500:]
            HK_PRICE_STATE["progress"] = "失败"
    except subprocess.TimeoutExpired:
        HK_PRICE_STATE["status"] = "error"
        HK_PRICE_STATE["last_error"] = "超时（10 分钟）"
        HK_PRICE_STATE["progress"] = "超时"
    except Exception as e:
        HK_PRICE_STATE["status"] = "error"
        HK_PRICE_STATE["last_error"] = str(e)
        HK_PRICE_STATE["progress"] = "异常"


@router.post("/system/sync-hk-prices")
def sync_hk_prices(body: dict = {}):
    """触发港股历史日线补全（后台运行）"""
    with _hk_price_lock:
        if HK_PRICE_STATE["status"] == "running":
            return {"status": "warning", "message": "港股入库正在运行，请稍候"}

        try:
            start, codes = _normalize_hk_sync_args(body.get("start"), body.get("codes"))
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # 在锁内原子置位，再启线程 — 避免并发双触发
        HK_PRICE_STATE["status"] = "running"
        HK_PRICE_STATE["last_error"] = None
        HK_PRICE_STATE["progress"] = "正在拉取前复权全历史日线..."
        HK_PRICE_STATE["rows_written"] = 0
        HK_PRICE_STATE["last_stdout"] = ""

        t = threading.Thread(target=_run_hk_price_sync, args=(start, codes), daemon=True)
        t.start()
    return {"status": "success", "message": "港股日线入库已启动"}


@router.get("/system/hk-price-status")
def get_hk_price_status():
    """返回港股日线入库状态"""
    return {"status": "success", "data": HK_PRICE_STATE.copy()}
