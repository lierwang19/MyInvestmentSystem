import asyncio

from fastapi import HTTPException
from starlette.requests import Request

from web.backend.response import http_exception_handler, unhandled_exception_handler


def _make_request(path: str = "/api/test") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_http_exception_handler_hides_5xx_detail():
    request = _make_request()
    response = asyncio.run(
        http_exception_handler(request, HTTPException(status_code=500, detail="sqlite error: table missing"))
    )
    body = response.body.decode("utf-8")
    assert "sqlite error" not in body
    assert "服务器内部错误" in body


def test_unhandled_exception_handler_hides_exception_text():
    request = _make_request()
    response = asyncio.run(unhandled_exception_handler(request, RuntimeError("secret stack detail")))
    body = response.body.decode("utf-8")
    assert "secret stack detail" not in body
    assert "服务器内部错误" in body
