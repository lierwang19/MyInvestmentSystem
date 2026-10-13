"""行情更新 / 因子构建子进程超时：记录错误状态、不向外抛异常、释放写锁。"""
import subprocess

import pytest

from web.backend import drip_sync, scheduler
from web.backend.services import trading_calendar


@pytest.fixture
def sched(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(scheduler, "SCHEDULER_LAST_RUN", {})
    monkeypatch.setattr(trading_calendar, "is_a_trading_day", lambda: True)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    return calls


def test_market_update_timeout_records_error_and_releases_lock(sched, monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "_build_calibrator_base", lambda base, db: (False, None, ""))

    scheduler.run_daily_market_update()

    assert sched[0]["timeout"] == scheduler.MARKET_UPDATE_TIMEOUT_SEC == 10800
    entry = scheduler.SCHEDULER_LAST_RUN["market_update"]
    assert entry["status"] == "error"
    assert "market_update" in entry["detail"] and "10800" in entry["detail"]
    assert drip_sync.market_db_write_lock.acquire(blocking=False)
    drip_sync.market_db_write_lock.release()
    assert scheduler._market_update_done.is_set()


def test_factor_build_timeout_records_error(sched, monkeypatch):
    monkeypatch.setattr(scheduler, "_upstream_ok_today", lambda job_id: True)
    scheduler._valuation_done.set()

    scheduler.run_factor_build()

    assert sched[0]["timeout"] == scheduler.FACTOR_BUILD_TIMEOUT_SEC == 3600
    entry = scheduler.SCHEDULER_LAST_RUN["factor_build"]
    assert entry["status"] == "error"
    assert "factor_build" in entry["detail"] and "3600" in entry["detail"]
    assert scheduler._factor_done.is_set()
