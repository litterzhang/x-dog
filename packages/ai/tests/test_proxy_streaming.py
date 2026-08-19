from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from xdog.ai.proxy import _handle_connection
from xdog.ai.types import (
    AssistantMessage,
    AuthExpiredError,
    DoneEvent,
    ErrorEvent,
    StartEvent,
    TextDeltaEvent,
    TextDoneEvent,
    TextStartEvent,
)

StreamFactory = Callable[[], AsyncIterator[Any]]


class ScriptedProvider:
    def __init__(self, attempts: list[StreamFactory]) -> None:
        self._attempts = iter(attempts)
        self.calls = 0

    def stream(self, model_id: str, context: Any, options: Any) -> AsyncIterator[Any]:
        self.calls += 1
        return next(self._attempts)()


def _request_bytes() -> bytes:
    body = json.dumps({
        "model": "test-model",
        "max_tokens": 16,
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
    }).encode()
    return (
        b"POST /v1/messages HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )


async def _request(provider: Any) -> tuple[int, dict[str, str], bytes]:
    async def on_connect(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await _handle_connection(reader, writer, provider)

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    try:
        socket = server.sockets[0]
        host, port = socket.getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(_request_bytes())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=2)
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    head, separator, body = raw.partition(b"\r\n\r\n")
    assert separator, raw
    lines = head.decode().split("\r\n")
    status = int(lines[0].split()[1])
    headers = {
        key.lower(): value.strip()
        for line in lines[1:]
        if ":" in line
        for key, value in [line.split(":", 1)]
    }
    return status, headers, body


def _sse_event_names(body: bytes) -> list[str]:
    return [
        line.removeprefix("event: ")
        for line in body.decode().splitlines()
        if line.startswith("event: ")
    ]


async def _ssl_failure() -> AsyncIterator[Any]:
    raise ssl.SSLError(1, "TLS read failed")
    yield


async def _auth_failure() -> AsyncIterator[Any]:
    raise AuthExpiredError("GitHub Copilot", "xdog-ai login copilot")
    yield


async def _empty_stream() -> AsyncIterator[Any]:
    return
    yield


async def _initial_error() -> AsyncIterator[Any]:
    yield ErrorEvent(
        error=(
            'HTTP 429: {"type":"error","error":'
            '{"type":"rate_limit_error","message":"slow down"}}'
        ),
    )


async def _start_then_failure() -> AsyncIterator[Any]:
    yield StartEvent(partial=AssistantMessage())
    raise ssl.SSLError(1, "TLS failed before content")


async def _partial_then_failure() -> AsyncIterator[Any]:
    partial = AssistantMessage()
    yield StartEvent(partial=partial)
    yield TextStartEvent(index=0, partial=partial)
    yield TextDeltaEvent(index=0, delta="partial", partial=partial)
    raise ssl.SSLError(1, "TLS read failed after content")


async def _successful_stream() -> AsyncIterator[Any]:
    partial = AssistantMessage()
    yield StartEvent(partial=partial)
    yield TextStartEvent(index=0, partial=partial)
    yield TextDeltaEvent(index=0, delta="OK", partial=partial)
    yield TextDoneEvent(index=0, text="OK", partial=partial)
    yield DoneEvent(
        stop_reason="stop",
        message=AssistantMessage(stop_reason="stop"),
    )


@pytest.mark.asyncio
async def test_initial_ssl_failure_retries_then_returns_gateway_json(monkeypatch):
    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    provider = ScriptedProvider([_ssl_failure, _ssl_failure, _ssl_failure])

    status, headers, body = await _request(provider)

    assert provider.calls == 3
    assert status == 502
    assert headers["content-type"] == "application/json"
    assert headers["connection"] == "close"
    assert json.loads(body)["error"] == {
        "type": "api_error",
        "message": "Upstream connection failed; retry later",
    }
    assert b"event: " not in body


@pytest.mark.asyncio
async def test_auth_expiry_returns_401_without_retry():
    provider = ScriptedProvider([_auth_failure, _successful_stream])

    status, headers, body = await _request(provider)

    assert provider.calls == 1
    assert status == 401
    assert headers["content-type"] == "application/json"
    error = json.loads(body)["error"]
    assert error["type"] == "authentication_error"
    assert "xdog-ai login copilot" in error["message"]


@pytest.mark.asyncio
async def test_empty_stream_returns_gateway_json():
    provider = ScriptedProvider([_empty_stream])

    status, headers, body = await _request(provider)

    assert provider.calls == 1
    assert status == 502
    assert headers["content-type"] == "application/json"
    assert json.loads(body)["error"]["type"] == "api_error"
    assert b"event: " not in body


@pytest.mark.asyncio
async def test_initial_error_event_preserves_upstream_status():
    provider = ScriptedProvider([_initial_error])

    status, headers, body = await _request(provider)

    assert provider.calls == 1
    assert status == 429
    assert headers["content-type"] == "application/json"
    assert json.loads(body)["error"] == {
        "type": "rate_limit_error",
        "message": "slow down",
    }


@pytest.mark.asyncio
async def test_start_event_alone_does_not_commit_response(monkeypatch):
    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    provider = ScriptedProvider([_start_then_failure, _successful_stream])

    status, headers, body = await _request(provider)

    assert provider.calls == 2
    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert _sse_event_names(body) == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]


@pytest.mark.asyncio
async def test_post_start_failure_is_terminal_sse_without_retry():
    provider = ScriptedProvider([_partial_then_failure, _successful_stream])

    status, headers, body = await _request(provider)

    assert provider.calls == 1
    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert headers["connection"] == "close"
    assert "content-length" not in headers
    assert _sse_event_names(body) == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "error",
    ]
    assert body.count(b"event: error") == 1
    assert b"message_stop" not in body


@pytest.mark.asyncio
async def test_successful_sse_has_ordered_events_and_close_framing():
    provider = ScriptedProvider([_successful_stream])

    status, headers, body = await _request(provider)

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert headers["connection"] == "close"
    assert "content-length" not in headers
    assert body.endswith(b"\n\n")
    assert _sse_event_names(body) == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
