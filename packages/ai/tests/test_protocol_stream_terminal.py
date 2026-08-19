from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.protocols.anthropic_messages import _stream_impl
from xdog.ai.types import Context, Model, StreamOptions


async def _serve_sse(frames: list[tuple[str, dict[str, object]]]):
    async def on_connect(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b"".join(
            f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
            for event, data in frames
        )
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    socket = server.sockets[0]
    host, port = socket.getsockname()[:2]
    return server, f"http://{host}:{port}"


@pytest.mark.asyncio
async def test_anthropic_terminal_error_does_not_emit_done():
    server, base_url = await _serve_sse([
        ("error", {
            "type": "error",
            "error": {"type": "overloaded_error", "message": "try later"},
        }),
    ])
    try:
        events = [
            event
            async for event in _stream_impl(
                Model(id="test", base_url=base_url),
                Context(),
                StreamOptions(),
                AuthResult(api_key="test"),
            )
        ]
    finally:
        server.close()
        await server.wait_closed()

    assert [event.type for event in events] == ["error"]
    assert events[0].error == "try later"


@pytest.mark.asyncio
async def test_anthropic_transport_failure_before_first_event_is_raised():
    server, base_url = await _serve_sse([])
    try:
        with pytest.raises(httpx.RemoteProtocolError):
            events = [
                event
                async for event in _stream_impl(
                    Model(id="test", base_url=base_url),
                    Context(),
                    StreamOptions(),
                    AuthResult(api_key="test"),
                )
            ]
            assert events == []
    finally:
        server.close()
        await server.wait_closed()
