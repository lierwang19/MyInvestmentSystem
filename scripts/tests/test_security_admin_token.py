import pytest
from fastapi import HTTPException

from web.backend.security import (
    is_single_user_mode,
    is_admin_auth_enabled,
    is_protected_write_request,
    require_admin_token,
    validate_admin_token,
)


def test_admin_token_dependency_rejects_missing_or_invalid(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")

    assert is_admin_auth_enabled() is True

    with pytest.raises(HTTPException) as missing:
        require_admin_token(None)
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as wrong:
        require_admin_token("wrong")
    assert wrong.value.status_code == 401

    assert require_admin_token("secret-token") is None


def test_admin_token_dependency_is_disabled_without_env(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("SINGLE_USER_MODE", raising=False)

    assert is_admin_auth_enabled() is False
    assert require_admin_token(None) is None


def test_single_user_mode_disables_admin_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    monkeypatch.setenv("SINGLE_USER_MODE", "1")

    assert is_single_user_mode() is True
    assert is_admin_auth_enabled() is False
    assert require_admin_token(None) is None
    assert validate_admin_token(None) is None
    assert validate_admin_token("wrong") is None


def test_validate_admin_token_and_write_scope(monkeypatch):
    monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")

    assert is_protected_write_request("POST", "/api/accounts-db") is True
    assert is_protected_write_request("DELETE", "/api/watchlist/1") is True
    assert is_protected_write_request("GET", "/api/system/backup") is False
    assert is_protected_write_request("OPTIONS", "/api/accounts-db") is False
    assert is_protected_write_request("POST", "/healthz") is False

    assert validate_admin_token("secret-token") is None

    with pytest.raises(HTTPException) as missing:
        validate_admin_token(None)
    assert missing.value.status_code == 401
