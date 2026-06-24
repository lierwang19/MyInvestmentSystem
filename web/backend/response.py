# -*- coding: utf-8 -*-
"""
Standardized API response helpers and exception handler.
Provides consistent response format across all API endpoints.

Usage:
    from web.backend.response import success, error, APIError

    @router.get("/example")
    def example():
        return success(data={"count": 42}, message="OK")

    @router.post("/example")
    def create_example():
        if bad_input:
            raise APIError("Invalid input", status_code=400)
        return success(data=new_item, message="Created")
"""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Any, Optional
import logging
import traceback

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Raise in any endpoint to return a structured error response."""
    def __init__(self, message: str, status_code: int = 400, detail: Any = None):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


def success(data: Any = None, message: str = "success", **extra) -> dict:
    """Build a success response dict."""
    resp = {"status": "success", "message": message}
    if data is not None:
        resp["data"] = data
    resp.update(extra)
    return resp


def error(message: str = "error", code: int = 500, detail: Any = None, **extra) -> dict:
    """Build an error response dict (for returning in-endpoint)."""
    resp = {"status": "error", "message": message}
    if detail is not None:
        resp["detail"] = detail
    resp.update(extra)
    return resp


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """Global handler for APIError exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "message": exc.message,
            "detail": exc.detail,
        },
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Sanitize 5xx HTTPException details while preserving 4xx validation feedback."""
    detail = exc.detail
    if exc.status_code >= 500:
        logger.error(f"HTTPException {exc.status_code} on {request.method} {request.url.path}: {exc.detail}")
        detail = "服务器内部错误"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "message": "error",
            "detail": detail,
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global handler for unhandled exceptions — log traceback and return 500."""
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}")
    logger.error(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": "服务器内部错误",
            "detail": "服务器内部错误",
        },
    )
