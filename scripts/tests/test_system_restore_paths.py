from pathlib import Path

from web.backend.api.system import _normalize_restore_member_name, _resolve_restore_target
from web.backend.config import PROJECT_ROOT


def test_normalize_restore_member_name_allows_expected_paths():
    assert _normalize_restore_member_name("user_data.db") == "user_data.db"
    assert _normalize_restore_member_name("config/app.yaml") == "config/app.yaml"
    assert _normalize_restore_member_name("config/nested/rules.json") == "config/nested/rules.json"


def test_normalize_restore_member_name_rejects_unsafe_paths():
    assert _normalize_restore_member_name("") is None
    assert _normalize_restore_member_name("/etc/passwd") is None
    assert _normalize_restore_member_name("../user_data.db") is None
    assert _normalize_restore_member_name("config/../secrets.txt") is None
    assert _normalize_restore_member_name("config/") is None
    assert _normalize_restore_member_name("market.db") is None


def test_resolve_restore_target_stays_under_project_root():
    project_root = Path(PROJECT_ROOT).resolve()

    assert _resolve_restore_target("user_data.db") == project_root / "user_data.db"
    assert _resolve_restore_target("config/app.yaml") == project_root / "config" / "app.yaml"
