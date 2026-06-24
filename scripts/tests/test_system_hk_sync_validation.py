import pytest

from web.backend.api.system import _normalize_hk_sync_args


def test_normalize_hk_sync_args_accepts_valid_values():
    start, codes = _normalize_hk_sync_args("20240101", ["09988.HK", "01810.hk", "09988.HK"])
    assert start == "20240101"
    assert codes == ["09988.HK", "01810.HK"]


def test_normalize_hk_sync_args_rejects_bad_values():
    with pytest.raises(ValueError):
        _normalize_hk_sync_args("2024-01-01", None)

    with pytest.raises(ValueError):
        _normalize_hk_sync_args("20240101", ["9988.HK"])

    too_many = [f"{i:05d}.HK" for i in range(201)]
    with pytest.raises(ValueError):
        _normalize_hk_sync_args("20240101", too_many)
