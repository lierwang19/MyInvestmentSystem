from web.backend import drip_sync


def test_subprocess_timeout_respects_interval_budget(monkeypatch):
    monkeypatch.setattr(
        "web.backend.api.settings.get_setting_value",
        lambda key, default="": {"drip_sync_interval_min": "8"}.get(key, default),
    )
    assert drip_sync._get_subprocess_timeout_seconds() == 420


def test_subprocess_timeout_has_floor(monkeypatch):
    monkeypatch.setattr(
        "web.backend.api.settings.get_setting_value",
        lambda key, default="": {"drip_sync_interval_min": "1"}.get(key, default),
    )
    assert drip_sync._get_subprocess_timeout_seconds() == 120


def test_subprocess_timeout_falls_back_when_setting_missing(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr("web.backend.api.settings.get_setting_value", _raise)
    assert drip_sync._get_subprocess_timeout_seconds() == 600
