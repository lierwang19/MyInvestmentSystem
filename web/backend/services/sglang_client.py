# -*- coding: utf-8 -*-
"""
工作站 sglang 客户端（OpenAI 兼容接口），供 AI 简报和 AI 风险分析共用。

本机经 SSH 隧道访问 127.0.0.1:18100；Docker 内由 docker-compose 指向 host.docker.internal。
Qwen3 思考模式默认关闭，以缩短耗时。
"""
import json
import os
import urllib.request

SGLANG_BASE_URL = (os.environ.get("SGLANG_BASE_URL", "").strip() or "http://127.0.0.1:18100").rstrip("/")
SGLANG_MODEL = os.environ.get("SGLANG_MODEL", "").strip() or "qwen"


def check_sglang() -> bool:
    """Return True if sglang is reachable."""
    try:
        urllib.request.urlopen(f"{SGLANG_BASE_URL}/v1/models", timeout=5)
        return True
    except Exception:
        return False


def unreachable_msg() -> str:
    return f"sglang 服务不可达（{SGLANG_BASE_URL}），请确认工作站 sglang 及 SSH 隧道已启动"


def _chat_request(prompt: str, stream: bool) -> urllib.request.Request:
    payload = json.dumps({
        "model": SGLANG_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "stream": stream,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode("utf-8")
    return urllib.request.Request(
        f"{SGLANG_BASE_URL}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )


def chat_sync(prompt: str, timeout: int = 300) -> str:
    """Return the complete answer text. Raises on failure."""
    with urllib.request.urlopen(_chat_request(prompt, stream=False), timeout=timeout) as resp:
        result = json.loads(resp.read())
    return (result["choices"][0]["message"].get("content") or "").strip()


def chat_stream(prompt: str, timeout: int = 180):
    """Yield answer text deltas as they arrive. Raises on upstream error or early EOF."""
    with urllib.request.urlopen(_chat_request(prompt, stream=True), timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                err = chunk["error"]
                raise RuntimeError(f"sglang 返回错误：{err.get('message', err) if isinstance(err, dict) else err}")
            choices = chunk.get("choices") or []
            delta = (choices[0].get("delta") or {}).get("content") if choices else None
            if delta:
                yield delta
    # 未收到 [DONE] 即结束：隧道或 sglang 中断，不能当作正常完成
    raise RuntimeError("生成中断：sglang 未返回结束标记 [DONE]")
