from web.backend import drip_sync


def test_effective_batch_size_respects_config(monkeypatch):
    monkeypatch.setattr(
        "web.backend.api.settings.get_setting_value",
        lambda key, default="": {"drip_sync_batch_size": "40"}.get(key, default),
    )
    drip_sync.DRIP_STATE["effective_batch_size"] = 80
    assert drip_sync._get_effective_batch_size() == 40


def test_timeout_halves_effective_batch_size():
    drip_sync.DRIP_STATE["effective_batch_size"] = 40
    drip_sync.DRIP_STATE["consecutive_success_slices"] = 2
    drip_sync._record_slice_feedback(had_timeout=True, configured_batch_size=40)
    assert drip_sync.DRIP_STATE["effective_batch_size"] == 20
    assert drip_sync.DRIP_STATE["consecutive_success_slices"] == 0


def test_success_grows_effective_batch_size_after_three_clean_slices():
    drip_sync.DRIP_STATE["effective_batch_size"] = 20
    drip_sync.DRIP_STATE["consecutive_success_slices"] = 0

    drip_sync._record_slice_feedback(had_timeout=False, configured_batch_size=40)
    drip_sync._record_slice_feedback(had_timeout=False, configured_batch_size=40)
    assert drip_sync.DRIP_STATE["effective_batch_size"] == 20

    drip_sync._record_slice_feedback(had_timeout=False, configured_batch_size=40)
    assert drip_sync.DRIP_STATE["effective_batch_size"] == 25
    assert drip_sync.DRIP_STATE["consecutive_success_slices"] == 0
