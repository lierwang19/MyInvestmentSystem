from datetime import timedelta

from web.backend.api import market


def test_get_current_market_skips_repeated_tushare_failures(monkeypatch):
    calls = {"daily_basic": 0}

    class DummyPro:
        def daily_basic(self, **kwargs):
            calls["daily_basic"] += 1
            raise RuntimeError("no permission")

    monkeypatch.setattr(market, "_TS_DAILY_BASIC_BACKOFF_UNTIL", None)
    monkeypatch.setattr(market, "_TS_DAILY_BASIC_LAST_ERROR", None)
    monkeypatch.setattr(market, "_init_ts", lambda: True)
    monkeypatch.setattr(market.ts, "pro_api", lambda: DummyPro())
    monkeypatch.setattr(
        market,
        "_get_current_from_market_db",
        lambda: {
            "trade_date": "2026-04-15",
            "pb": 1.5,
            "target_position": 0.6,
            "target_position_pct": "60.0%",
            "source": "local_db",
        },
    )

    first = market.get_current_market()
    assert first["status"] == "success"
    assert first["data"]["source"] == "local_db"
    assert calls["daily_basic"] == 1
    assert market._TS_DAILY_BASIC_BACKOFF_UNTIL is not None

    second = market.get_current_market()
    assert second["status"] == "success"
    assert second["data"]["source"] == "local_db"
    assert calls["daily_basic"] == 1


def test_permission_denied_failure_uses_long_backoff(monkeypatch):
    calls = {"daily_basic": 0}

    class DummyPro:
        def daily_basic(self, **kwargs):
            calls["daily_basic"] += 1
            raise RuntimeError("抱歉，您没有接口访问权限")

    monkeypatch.setattr(market, "_TS_DAILY_BASIC_BACKOFF_UNTIL", None)
    monkeypatch.setattr(market, "_TS_DAILY_BASIC_LAST_ERROR", None)
    monkeypatch.setattr(market, "_init_ts", lambda: True)
    monkeypatch.setattr(market.ts, "pro_api", lambda: DummyPro())
    monkeypatch.setattr(
        market,
        "_get_current_from_market_db",
        lambda: {
            "trade_date": "2026-04-15",
            "pb": 1.5,
            "target_position": 0.6,
            "target_position_pct": "60.0%",
            "source": "local_db",
        },
    )

    before = market.sh_now()
    result = market.get_current_market()
    after = market._TS_DAILY_BASIC_BACKOFF_UNTIL

    assert result["status"] == "success"
    assert result["data"]["source"] == "local_db"
    assert calls["daily_basic"] == 1
    assert after is not None
    assert (after - before).total_seconds() >= 23 * 3600


def test_get_current_market_clears_backoff_after_success(monkeypatch):
    calls = {"daily_basic": 0}

    class DummyPro:
        def daily_basic(self, **kwargs):
            calls["daily_basic"] += 1
            import pandas as pd

            return pd.DataFrame(
                [
                    {"ts_code": "600000.SH", "total_mv": 100.0, "pb": 1.0},
                    {"ts_code": "000001.SZ", "total_mv": 100.0, "pb": 1.0},
                ]
            )

    monkeypatch.setattr(market, "_init_ts", lambda: True)
    monkeypatch.setattr(market.ts, "pro_api", lambda: DummyPro())
    monkeypatch.setattr(market, "_TS_DAILY_BASIC_LAST_ERROR", "old error")
    monkeypatch.setattr(
        market,
        "_TS_DAILY_BASIC_BACKOFF_UNTIL",
        market.sh_now() - timedelta(minutes=1),
    )

    result = market.get_current_market()
    assert result["status"] == "success"
    assert result["data"]["stock_count"] == 2
    assert market._TS_DAILY_BASIC_BACKOFF_UNTIL is None
    assert market._TS_DAILY_BASIC_LAST_ERROR is None
    assert calls["daily_basic"] == 1
