"""工作站 sglang（OpenAI 兼容接口）：AI 简报整段生成、AI 风险分析流式输出。"""
import io
import json

import pytest

from web.backend.api import ai_analysis
from web.backend.services import sglang_client

BASE = "http://host.docker.internal:18100"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _sse(*deltas):
    lines = [b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n', b"\n"]
    for d in deltas:
        lines += [("data: " + json.dumps({"choices": [{"delta": {"content": d}}]}) + "\n").encode(), b"\n"]
    return lines + [b"data: [DONE]\n", b"\n"]


@pytest.fixture
def requests(monkeypatch):
    monkeypatch.setattr(sglang_client, "SGLANG_BASE_URL", BASE)
    monkeypatch.setattr(sglang_client, "SGLANG_MODEL", "qwen")
    sent = []

    def fake_urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        body = None if isinstance(req, str) else json.loads(req.data)
        sent.append({"url": url, "body": body, "timeout": timeout})
        if body and body["stream"]:
            return _Resp(b"".join(_sse("1. 估值", "：低估\n2. 风险", "：无")))
        if body:
            return _Resp(json.dumps({"choices": [{"message": {
                "role": "assistant", "content": "  估值偏低，建议持有。\n", "reasoning_content": None}}]}).encode())
        return _Resp(b"{}")

    monkeypatch.setattr(sglang_client.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_chat_sync_request_and_parse(requests):
    assert sglang_client.check_sglang()
    assert sglang_client.chat_sync("点评平安银行", timeout=300) == "估值偏低，建议持有。"

    check, call = requests
    assert check["url"] == f"{BASE}/v1/models"
    assert call["url"] == f"{BASE}/v1/chat/completions"
    assert call["timeout"] == 300
    assert call["body"]["model"] == "qwen"
    assert call["body"]["stream"] is False
    assert call["body"]["messages"] == [{"role": "user", "content": "点评平安银行"}]
    assert call["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_chat_stream_yields_content_deltas(requests):
    assert list(sglang_client.chat_stream("x")) == ["1. 估值", "：低估\n2. 风险", "：无"]
    assert requests[0]["body"]["stream"] is True


def _parse_sse(events: str) -> str:
    """与前端 useAiAnalysis.ts 相同的解析规则：事件内多行 data 以换行拼接。"""
    out, data = "", []
    for line in events.split("\n"):
        if line.startswith("data: "):
            data.append(line[6:])
        elif line == "":
            out += "\n".join(data)
            data = []
    return out


def test_analysis_stream_preserves_newlines(requests):
    events = "".join(ai_analysis._stream_llm("x"))
    assert events.endswith("event: done\ndata: \n\n")
    assert _parse_sse(events) == "1. 估值：低估\n2. 风险：无"


def test_analysis_stream_reports_error(monkeypatch):
    def refuse(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(sglang_client.urllib.request, "urlopen", refuse)
    events = "".join(ai_analysis._stream_llm("x"))
    assert events.startswith("event: error\n")
    assert "connection refused" in _parse_sse(events)
    assert not sglang_client.check_sglang()
    assert "sglang" in sglang_client.unreachable_msg()


def _fake_stream(monkeypatch, lines):
    monkeypatch.setattr(sglang_client.urllib.request, "urlopen", lambda req, timeout=None: _Resp(b"".join(lines)))


def test_analysis_stream_early_eof_reports_error_and_keeps_partial(monkeypatch):
    lines = _sse("partial")
    _fake_stream(monkeypatch, lines[:-2])  # 去掉 [DONE]
    events = "".join(ai_analysis._stream_llm("x"))
    assert "event: done" not in events
    assert "event: error\n" in events
    text = _parse_sse(events)
    assert text.startswith("partial\n\nLLM调用异常: ")
    assert "生成中断" in text


def test_analysis_stream_upstream_error_reports_error(monkeypatch):
    _fake_stream(monkeypatch, [b'data: {"error": {"message": "context length exceeded"}}\n', b"\n"])
    events = "".join(ai_analysis._stream_llm("x"))
    assert "event: done" not in events
    assert "context length exceeded" in _parse_sse(events)
