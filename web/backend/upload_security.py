import re
from pathlib import Path

from fastapi import HTTPException, UploadFile


MAX_GENERIC_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024

_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_storage_component(value: str, field_name: str = "value") -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} 不能为空")
    if not _SAFE_COMPONENT_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail=f"{field_name} 包含非法字符")
    if normalized in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"{field_name} 非法")
    return normalized


def ensure_within_root(root: str, candidate: str) -> str:
    root_path = Path(root).resolve()
    candidate_path = Path(candidate).resolve()
    if candidate_path != root_path and root_path not in candidate_path.parents:
        raise HTTPException(status_code=400, detail="非法文件路径")
    return str(candidate_path)


async def read_limited_upload(file: UploadFile, max_bytes: int) -> bytes:
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"上传文件过大，最大允许 {max_bytes // (1024 * 1024)}MB")
    return data
