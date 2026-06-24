import os
from typing import Optional

from fastapi import Header, HTTPException


ADMIN_TOKEN_HEADER = "X-Admin-Token"


def is_single_user_mode() -> bool:
    value = os.environ.get("SINGLE_USER_MODE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_admin_token() -> str:
    return os.environ.get("ADMIN_TOKEN", "").strip()


def is_admin_auth_enabled() -> bool:
    if is_single_user_mode():
        return False
    return bool(get_admin_token())


def is_protected_write_request(method: str, path: str) -> bool:
    if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    return path.startswith("/api/")


def validate_admin_token(token: Optional[str]) -> None:
    if is_single_user_mode():
        return
    expected = get_admin_token()
    if not expected:
        return
    if token != expected:
        raise HTTPException(status_code=401, detail="需要管理员令牌")


def require_admin_token(x_admin_token: Optional[str] = Header(default=None)) -> None:
    validate_admin_token(x_admin_token)
