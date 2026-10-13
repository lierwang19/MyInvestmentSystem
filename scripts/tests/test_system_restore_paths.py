from pathlib import Path

import pytest

import io
import zipfile

from web.backend.api.system import (
    _collect_restore_pairs,
    _normalize_restore_member_name,
    _resolve_restore_target,
)
from web.backend.config import PROJECT_ROOT


def test_normalize_restore_member_name_allows_expected_paths():
    assert _normalize_restore_member_name("data/user_data.db") == "data/user_data.db"
    # 兼容旧版备份包：zip 根目录的 user_data.db 映射到新位置
    assert _normalize_restore_member_name("user_data.db") == "data/user_data.db"
    assert _normalize_restore_member_name("config/app.yaml") == "config/app.yaml"
    assert _normalize_restore_member_name("config/nested/rules.json") == "config/nested/rules.json"


def test_normalize_restore_member_name_rejects_unsafe_paths():
    assert _normalize_restore_member_name("") is None
    assert _normalize_restore_member_name("/etc/passwd") is None
    assert _normalize_restore_member_name("../user_data.db") is None
    assert _normalize_restore_member_name("data/../user_data.db") is None
    assert _normalize_restore_member_name("config/../secrets.txt") is None
    assert _normalize_restore_member_name("config/") is None
    assert _normalize_restore_member_name("market.db") is None
    assert _normalize_restore_member_name("data/market.db") is None


def test_collect_restore_pairs_uses_original_name_for_read():
    # W4 回归：旧备份包里 user_data.db 在 zip 根目录，读取必须用原始名，
    # 落盘才用映射后的 data/user_data.db
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("user_data.db", b"fake-db-bytes")
        zf.writestr("config/app.yaml", b"key: value")
        zf.writestr("evil/../x.db", b"nope")
    buf.seek(0)
    with zipfile.ZipFile(buf, mode="r") as zf:
        pairs = _collect_restore_pairs(zf)
    assert ("user_data.db", "data/user_data.db") in pairs
    assert ("config/app.yaml", "config/app.yaml") in pairs
    # 读取侧用原始名一定能在 zip 里找到
    buf.seek(0)
    with zipfile.ZipFile(buf, mode="r") as zf:
        names = zf.namelist()
        for original, _target in pairs:
            assert original in names
            assert zf.open(original).read() is not None


def test_resolve_restore_target_stays_under_project_root():
    project_root = Path(PROJECT_ROOT).resolve()

    assert _resolve_restore_target("data/user_data.db") == project_root / "data" / "user_data.db"
    assert _resolve_restore_target("config/app.yaml") == project_root / "config" / "app.yaml"
    # 旧名在 resolve 层仍可解析（实际流程中 normalize 已先映射到 data/）
    assert _resolve_restore_target("user_data.db") == project_root / "user_data.db"
    with pytest.raises(ValueError):
        _resolve_restore_target("../evil.db")
