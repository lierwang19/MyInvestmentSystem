import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from web.backend.upload_security import (
    ensure_within_root,
    read_limited_upload,
    validate_storage_component,
)


def test_validate_storage_component_rejects_path_traversal():
    assert validate_storage_component("A-02", "account_id") == "A-02"

    with pytest.raises(HTTPException) as exc:
        validate_storage_component("../escape", "account_id")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc2:
        validate_storage_component("abc/def", "account_id")
    assert exc2.value.status_code == 400


def test_ensure_within_root_rejects_escaped_path(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()

    ok = ensure_within_root(str(root), str(root / "holdings" / "A-02" / "file.csv"))
    assert ok.endswith("file.csv")

    with pytest.raises(HTTPException) as exc:
        ensure_within_root(str(root), str(root / ".." / "escape.csv"))
    assert exc.value.status_code == 400


def test_read_limited_upload_rejects_oversize():
    small = UploadFile(filename="ok.csv", file=io.BytesIO(b"12345"))
    assert asyncio.run(read_limited_upload(small, 5)) == b"12345"

    large = UploadFile(filename="big.csv", file=io.BytesIO(b"abcdef"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_limited_upload(large, 5))
    assert exc.value.status_code == 413
