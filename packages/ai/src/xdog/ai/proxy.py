"""Anthropic API proxy — exposes /v1/messages backed by the ai package.

Accepts requests in the Anthropic Messages API format, routes them through
the ai package's multi-provider backend, and streams responses back as
Anthropic-compatible SSE events.

Usage::

    python -m ai.proxy --port 8082
    python -m ai.proxy --port 8082 --provider copilot

Then point any Anthropic SDK client at ``http://localhost:8082``::

    from anthropic import Anthropic
    client = Anthropic(api_key="dummy", base_url="http://localhost:8082")
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=100,
    )

Zero external dependencies beyond what the ai package already uses.
Uses raw asyncio TCP server with manual HTTP/1.1 parsing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from xdog.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    StreamOptions,
    TextContent,
    TextDoneEvent,
    ThinkingBudgets,
    ThinkingContent,
    ThinkingDoneEvent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thinking budget → level mapping (derived from ThinkingBudgets)
# ---------------------------------------------------------------------------

def _budget_to_thinking_level(budget: int) -> str:
    """Map a budget_tokens value to a ThinkingLevel string.

    Uses the thresholds defined in :class:`ThinkingBudgets` so they stay
    in sync automatically.  Picks the highest level whose budget is ≤ the
    requested budget.
    """
    budgets = ThinkingBudgets()
    # Ordered from highest to lowest so we return the best match
    levels = [
        ("xhigh", budgets.xhigh),
        ("high", budgets.high),
        ("medium", budgets.medium),
        ("low", budgets.low),
        ("minimal", budgets.minimal),
    ]
    for level, threshold in levels:
        if budget >= threshold:
            return level
    return "minimal"


# ---------------------------------------------------------------------------
# Request parsing: Anthropic JSON → ai types
# ---------------------------------------------------------------------------

def parse_request(body: dict[str, Any]) -> tuple[str, Context, StreamOptions, bool]:
    """Convert an Anthropic Messages API request body to ai types.

    Returns (model_id, context, options, is_stream).
    """
    model_id = body.get("model", "")
    is_stream = body.get("stream", False)

    # System prompt
    system_raw = body.get("system")
    if isinstance(system_raw, str):
        system_prompt = system_raw
    elif isinstance(system_raw, list):
        # List of content blocks — concatenate text
        system_prompt = "\n\n".join(
            b.get("text", "") for b in system_raw if b.get("type") == "text"
        )
    else:
        system_prompt = None

    # Messages
    messages = tuple(
        parsed
        for m in body.get("messages", [])
        for parsed in _parse_message(m)
    )

    # Tools
    tools = None
    raw_tools = body.get("tools")
    if raw_tools:
        tools = tuple(_parse_tool(t) for t in raw_tools)

    context = Context(
        system_prompt=system_prompt,
        messages=messages,
        tools=tools,
    )

    # Stream options
    thinking_raw = body.get("thinking")
    thinking_level = None
    if isinstance(thinking_raw, dict):
        thinking_type = thinking_raw.get("type", "")
        if thinking_type == "enabled":
            budget = thinking_raw.get("budget_tokens", 0)
            thinking_level = _budget_to_thinking_level(budget)
        elif thinking_type == "adaptive":
            # Adaptive thinking: effort level comes from output_config
            output_config = body.get("output_config", {})
            effort = output_config.get("effort", "medium") if isinstance(output_config, dict) else "medium"
            # Map Anthropic effort to our thinking level
            effort_to_level = {"low": "low", "medium": "medium", "high": "high"}
            thinking_level = effort_to_level.get(effort, "medium")

    options = StreamOptions(
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
        thinking=thinking_level,
    )

    return model_id, context, options, is_stream


def _parse_message(msg: dict[str, Any]) -> list[UserMessage | AssistantMessage | ToolResultMessage]:
    """Parse a single Anthropic message dict into a list of ai Messages.

    A single Anthropic ``user`` message may bundle multiple ``tool_result``
    blocks (parallel tool calls) alongside text/image parts. Each
    ``tool_result`` becomes its own :class:`ToolResultMessage`; any remaining
    text/image parts become a single :class:`UserMessage`. Returning a list
    ensures every ``tool_use`` id issued by the assistant is answered, which
    the upstream Anthropic API strictly requires.
    """
    role = msg.get("role", "")
    content = msg.get("content", "")

    if role == "user":
        if isinstance(content, str):
            return [UserMessage(content=content)]
        # Content blocks
        parts: list[Any] = []
        tool_results: list[ToolResultMessage] = []
        for block in content:
            btype = block.get("type", "")
            if btype == "text":
                parts.append(TextContent(text=block.get("text", "")))
            elif btype == "image":
                source = block.get("source", {})
                parts.append(ImageContent(
                    data=source.get("data", ""),
                    mime_type=source.get("media_type", "image/png"),
                ))
            elif btype == "tool_result":
                # Tool results are wrapped as ToolResultMessage
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    tc = (TextContent(text=result_content),)
                elif isinstance(result_content, list):
                    tc = tuple(
                        TextContent(text=b.get("text", ""))
                        for b in result_content if b.get("type") == "text"
                    )
                else:
                    tc = (TextContent(text=str(result_content)),)
                tool_results.append(ToolResultMessage(
                    tool_call_id=block.get("tool_use_id", ""),
                    tool_name="",
                    content=tc,
                    is_error=block.get("is_error", False),
                ))
        # Emit every tool_result (parallel tool calls), then any text/image parts.
        out: list[UserMessage | AssistantMessage | ToolResultMessage] = list(tool_results)
        if parts:
            out.append(UserMessage(content=tuple(parts)))
        if not out:
            out.append(UserMessage(content=""))
        return out

    if role == "assistant":
        if isinstance(content, str):
            return [AssistantMessage(content=(TextContent(text=content),))]
        parts_out: list[Any] = []
        for block in content:
            btype = block.get("type", "")
            if btype == "text":
                parts_out.append(TextContent(text=block.get("text", "")))
            elif btype == "thinking":
                parts_out.append(ThinkingContent(
                    thinking=block.get("thinking", ""),
                    thinking_signature=block.get("signature"),
                ))
            elif btype == "tool_use":
                parts_out.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))
        return [AssistantMessage(content=tuple(parts_out))]

    return [UserMessage(content=str(content))]


def _parse_tool(tool: dict[str, Any]) -> Tool:
    """Parse an Anthropic tool definition."""
    return Tool(
        name=tool.get("name", ""),
        description=tool.get("description", ""),
        parameters=tool.get("input_schema", {}),
    )


# ---------------------------------------------------------------------------
# Response formatting: ai events → Anthropic SSE
# ---------------------------------------------------------------------------

def _sse_line(event: str, data: dict[str, Any]) -> bytes:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _parse_upstream_error(error_message: str) -> tuple[int, dict[str, Any]]:
    """Recover an upstream Anthropic error envelope from an internal error."""
    status = 500
    payload_text = error_message
    if error_message.startswith("HTTP "):
        status_text, separator, payload_text = error_message[5:].partition(": ")
        if separator:
            try:
                status = int(status_text)
            except ValueError:
                status = 500

    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, TypeError):
        payload = None

    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return status, payload

    return status, {
        "type": "error",
        "error": {"type": "api_error", "message": error_message},
    }


def _map_stop_reason_to_anthropic(reason: str) -> str:
    """Map internal stop reasons to Anthropic API format."""
    return {
        "stop": "end_turn",
        "toolUse": "tool_use",
        "length": "max_tokens",
        "error": "end_turn",
        "aborted": "end_turn",
    }.get(reason, reason)


def _event_usage(event: Any) -> Any:
    """Return the Usage carried by a streaming event, if any."""
    message = getattr(event, "partial", None) or getattr(event, "message", None)
    return getattr(message, "usage", None)


def _message_start_line(msg_id: str, model_id: str, usage: Any) -> bytes:
    """Build the Anthropic ``message_start`` SSE line with real token usage."""
    return _sse_line("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model_id,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage.input if usage else 0,
                "output_tokens": usage.output if usage else 0,
                "cache_read_input_tokens": usage.cache_read if usage else 0,
                "cache_creation_input_tokens": usage.cache_write if usage else 0,
            },
        },
    })


async def stream_to_sse(
    event_stream: Any,
    model_id: str,
) -> AsyncIterator[bytes]:
    """Convert an ai EventStream to Anthropic SSE bytes.

    Yields SSE-formatted byte chunks matching the Anthropic Messages API
    streaming protocol.
    """
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    block_index = -1
    pending_message_start = False

    async for event in event_stream:
        etype = event.type

        if etype == "start":
            # Defer message_start until upstream usage is known — clients rely
            # on its input/cache token counts to track context growth.
            pending_message_start = True
            continue

        if pending_message_start:
            pending_message_start = False
            yield _message_start_line(msg_id, model_id, _event_usage(event))

        if etype == "text_start":
            block_index = event.index
            yield _sse_line("content_block_start", {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {"type": "text", "text": ""},
            })

        elif etype == "text_delta":
            yield _sse_line("content_block_delta", {
                "type": "content_block_delta",
                "index": event.index,
                "delta": {"type": "text_delta", "text": event.delta},
            })

        elif etype == "text_done":
            # Emit signature delta if present (before content_block_stop)
            assert isinstance(event, TextDoneEvent)
            if event.text_signature:
                yield _sse_line("content_block_delta", {
                    "type": "content_block_delta",
                    "index": event.index,
                    "delta": {
                        "type": "signature_delta",
                        "signature": event.text_signature,
                    },
                })
            yield _sse_line("content_block_stop", {
                "type": "content_block_stop",
                "index": event.index,
            })

        elif etype == "thinking_start":
            block_index = event.index
            yield _sse_line("content_block_start", {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {"type": "thinking", "thinking": ""},
            })

        elif etype == "thinking_delta":
            yield _sse_line("content_block_delta", {
                "type": "content_block_delta",
                "index": event.index,
                "delta": {"type": "thinking_delta", "thinking": event.delta},
            })

        elif etype == "thinking_done":
            # Emit signature delta if present (before content_block_stop)
            assert isinstance(event, ThinkingDoneEvent)
            if event.thinking_signature:
                yield _sse_line("content_block_delta", {
                    "type": "content_block_delta",
                    "index": event.index,
                    "delta": {
                        "type": "signature_delta",
                        "signature": event.thinking_signature,
                    },
                })
            yield _sse_line("content_block_stop", {
                "type": "content_block_stop",
                "index": event.index,
            })

        elif etype == "tool_call_start":
            block_index = event.index
            yield _sse_line("content_block_start", {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": event.id,
                    "name": event.name,
                    "input": {},
                },
            })

        elif etype == "tool_call_delta":
            yield _sse_line("content_block_delta", {
                "type": "content_block_delta",
                "index": event.index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": event.delta,
                },
            })

        elif etype == "tool_call_done":
            yield _sse_line("content_block_stop", {
                "type": "content_block_stop",
                "index": event.index,
            })

        elif etype == "usage":
            # Cumulative usage is forwarded in message_delta below, which the
            # OpenAI-backed protocols only know once the stream completes.
            pass

        elif etype == "done":
            assert isinstance(event, DoneEvent)
            msg = event.message
            stop_reason = _map_stop_reason_to_anthropic(
                msg.stop_reason if msg else "end_turn"
            )
            usage = msg.usage if msg else None

            yield _sse_line("message_delta", {
                "type": "message_delta",
                "delta": {
                    "stop_reason": stop_reason,
                    "stop_sequence": None,
                },
                "usage": {
                    "input_tokens": usage.input if usage else 0,
                    "output_tokens": usage.output if usage else 0,
                    "cache_read_input_tokens": usage.cache_read if usage else 0,
                    "cache_creation_input_tokens": usage.cache_write if usage else 0,
                },
            })
            yield _sse_line("message_stop", {"type": "message_stop"})

        elif etype == "error":
            assert isinstance(event, ErrorEvent)
            _, error = _parse_upstream_error(event.error)
            yield _sse_line("error", error)


def format_non_streaming_response(
    msg: AssistantMessage,
    model_id: str,
) -> dict[str, Any]:
    """Format an AssistantMessage as an Anthropic non-streaming JSON response."""
    content: list[dict[str, Any]] = []
    for part in msg.content:
        if isinstance(part, TextContent):
            block: dict[str, Any] = {"type": "text", "text": part.text}
            if part.text_signature:
                block["signature"] = part.text_signature
            content.append(block)
        elif isinstance(part, ThinkingContent):
            if part.redacted:
                content.append({"type": "redacted_thinking", "data": part.thinking or ""})
            else:
                tblock: dict[str, Any] = {"type": "thinking", "thinking": part.thinking or ""}
                if part.thinking_signature:
                    tblock["signature"] = part.thinking_signature
                content.append(tblock)
        elif isinstance(part, ToolCall):
            content.append({
                "type": "tool_use",
                "id": part.id,
                "name": part.name,
                "input": part.arguments or {},
            })

    stop_reason = _map_stop_reason_to_anthropic(msg.stop_reason or "end_turn")

    usage = msg.usage
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model_id,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.input if usage else 0,
            "output_tokens": usage.output if usage else 0,
            "cache_creation_input_tokens": usage.cache_write if usage else 0,
            "cache_read_input_tokens": usage.cache_read if usage else 0,
        },
    }


# ---------------------------------------------------------------------------
# HTTP server (raw asyncio, zero dependencies)
# ---------------------------------------------------------------------------

async def _read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], bytes] | None:
    """Read and parse an HTTP/1.1 request. Returns (method, path, headers, body)."""
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=30)
    except (asyncio.TimeoutError, ConnectionError):
        return None

    if not request_line:
        return None

    parts = request_line.decode("utf-8", errors="replace").strip().split(" ")
    if len(parts) < 2:
        return None

    method = parts[0]
    path = parts[1]

    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if not line or line == b"\r\n":
            break
        decoded = line.decode("utf-8", errors="replace").strip()
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    body = b""
    content_length = int(headers.get("content-length", "0"))
    if content_length > 0:
        body = await reader.readexactly(content_length)

    return method, path, headers, body


def _http_response(
    status: int,
    status_text: str,
    body: bytes,
    content_type: str = "application/json",
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Build a complete HTTP/1.1 response."""
    headers = [
        f"HTTP/1.1 {status} {status_text}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Access-Control-Allow-Origin: *",
        "Access-Control-Allow-Headers: *",
        "Access-Control-Allow-Methods: POST, OPTIONS",
        "Connection: close",
    ]
    if extra_headers:
        for k, v in extra_headers.items():
            headers.append(f"{k}: {v}")
    header_block = "\r\n".join(headers) + "\r\n\r\n"
    return header_block.encode() + body


def _sse_response_headers() -> bytes:
    """Build HTTP headers for an SSE streaming response."""
    headers = [
        "HTTP/1.1 200 OK",
        "Content-Type: text/event-stream",
        "Cache-Control: no-cache",
        "Connection: keep-alive",
        "Access-Control-Allow-Origin: *",
        "Access-Control-Allow-Headers: *",
        "Access-Control-Allow-Methods: POST, OPTIONS",
    ]
    return ("\r\n".join(headers) + "\r\n\r\n").encode()


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    provider: Any,
    api_key: str = "",
) -> None:
    """Handle a single HTTP connection."""
    try:
        req = await _read_http_request(reader)
        if req is None:
            writer.close()
            return

        method, path, headers, body = req

        # Strip query string for routing
        route_path = path.split("?")[0]

        logger.debug("Request: %s %s", method, path)

        # CORS preflight
        if method == "OPTIONS":
            writer.write(_http_response(204, "No Content", b""))
            await writer.drain()
            writer.close()
            return

        # Health check (no auth required)
        if route_path in ("/", "/health"):
            resp = json.dumps({"status": "ok"}).encode()
            writer.write(_http_response(200, "OK", resp))
            await writer.drain()
            writer.close()
            return

        # API key authentication
        if api_key:
            auth_header = headers.get("authorization", "")
            x_api_key = headers.get("x-api-key", "")
            provided = ""
            if auth_header.startswith("Bearer "):
                provided = auth_header[7:]
            elif x_api_key:
                provided = x_api_key
            if provided != api_key:
                resp = json.dumps({
                    "type": "error",
                    "error": {
                        "type": "authentication_error",
                        "message": "Invalid API key",
                    },
                }).encode()
                writer.write(_http_response(401, "Unauthorized", resp))
                await writer.drain()
                writer.close()
                return

        # GET /v1/models — list available models
        if method == "GET" and route_path == "/v1/models":
            models = _list_models(provider)
            resp = json.dumps(models).encode()
            writer.write(_http_response(200, "OK", resp))
            await writer.drain()
            writer.close()
            return

        # GET /v1/models/{model_id} — single model lookup (Claude Code validates on startup)
        if method == "GET" and route_path.startswith("/v1/models/"):
            model_id = route_path[len("/v1/models/"):]
            model_obj = _get_model(provider, model_id)
            if model_obj is not None:
                resp = json.dumps(model_obj).encode()
                writer.write(_http_response(200, "OK", resp))
            else:
                resp = json.dumps({
                    "type": "error",
                    "error": {"type": "not_found_error", "message": f"Model not found: {model_id}"},
                }).encode()
                writer.write(_http_response(404, "Not Found", resp))
            await writer.drain()
            writer.close()
            return

        # POST /v1/messages — Anthropic Messages API
        if method == "POST" and route_path == "/v1/messages":
            await _handle_messages(reader, writer, provider, body)
            return

        # Stub: /v1/organizations — Claude Code checks this at startup
        if route_path == "/v1/organizations":
            resp = json.dumps({"data": []}).encode()
            writer.write(_http_response(200, "OK", resp))
            await writer.drain()
            writer.close()
            return

        # Stub: /v1/dashboard — Claude Code billing/usage check
        if route_path.startswith("/v1/dashboard"):
            resp = json.dumps({}).encode()
            writer.write(_http_response(200, "OK", resp))
            await writer.drain()
            writer.close()
            return

        # Not found — respond immediately so clients don't hang
        logger.debug("Unhandled route: %s %s", method, path)
        resp = json.dumps({
            "type": "error",
            "error": {"type": "not_found", "message": f"Not found: {method} {path}"},
        }).encode()
        writer.write(_http_response(404, "Not Found", resp))
        await writer.drain()
        writer.close()

    except ConnectionResetError:
        # Client closed connection before we finished writing — harmless
        logger.debug("Client disconnected (connection reset)")
        try:
            writer.close()
        except Exception:
            pass

    except Exception as exc:
        logger.exception("Proxy request failed")
        try:
            resp = json.dumps({
                "type": "error",
                "error": {"type": "api_error", "message": str(exc)},
            }).encode()
            writer.write(_http_response(500, "Internal Server Error", resp))
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()


def _list_models(provider: Any) -> dict[str, Any]:
    """Build a model list response matching the Anthropic /v1/models format."""
    models = provider.models()
    data = []
    for m in models:
        data.append({
            "id": m.id,
            "type": "model",
            "display_name": m.name or m.id,
            "created_at": "2025-01-01T00:00:00Z",
        })
    return {
        "data": data,
        "has_more": False,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
    }


def _get_model(provider: Any, model_id: str) -> dict[str, Any] | None:
    """Look up a single model by ID, matching the Anthropic GET /v1/models/{id} format."""
    m = provider.model(model_id)
    if m is None:
        return None
    return {
        "id": m.id,
        "type": "model",
        "display_name": m.name or m.id,
        "created_at": "2025-01-01T00:00:00Z",
    }


async def _handle_messages(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    provider: Any,
    body: bytes,
) -> None:
    """Handle POST /v1/messages."""
    try:
        request_body = json.loads(body)
    except json.JSONDecodeError as exc:
        resp = json.dumps({
            "type": "error",
            "error": {"type": "invalid_request", "message": str(exc)},
        }).encode()
        writer.write(_http_response(400, "Bad Request", resp))
        await writer.drain()
        writer.close()
        return

    model_id, context, options, is_stream = parse_request(request_body)

    logger.info("Proxy request: model=%s stream=%s", model_id, is_stream)

    if is_stream:
        event_stream = provider.stream(model_id, context, options)

        writer.write(_sse_response_headers())
        await writer.drain()

        try:
            async for chunk in stream_to_sse(event_stream, model_id):
                writer.write(chunk)
                await writer.drain()
        except ConnectionResetError:
            logger.debug("Client disconnected during stream")
        except Exception as exc:
            logger.error("Stream error: %s", exc)
            try:
                writer.write(_sse_line("error", {
                    "type": "error",
                    "error": {"type": "api_error", "message": str(exc)},
                }))
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    else:
        msg = await provider.complete(model_id, context, options)
        if msg.stop_reason == "error" and msg.error_message:
            status, error = _parse_upstream_error(msg.error_message)
            resp = json.dumps(error).encode()
            writer.write(_http_response(status, "Bad Request" if status == 400 else "Upstream Error", resp))
        else:
            resp = json.dumps(format_non_streaming_response(msg, model_id)).encode()
            writer.write(_http_response(200, "OK", resp))
        await writer.drain()
        writer.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def start_proxy(
    host: str = "127.0.0.1",
    port: int = 8082,
    api_key: str = "",
) -> None:
    """Start the Anthropic API proxy server.

    Uses the ai Runtime, which routes model names to the correct provider
    automatically. Model names can be ``"provider/model"`` (explicit) or
    just ``"model"`` (if only one provider is active).

    Parameters
    ----------
    api_key:
        If set, all requests must include this key via
        ``Authorization: Bearer <key>`` or ``x-api-key: <key>``.
        If empty, no authentication is required.
    """
    import xdog.ai as ai
    runtime = ai.load()
    active = runtime.active_providers()
    if not active:
        raise RuntimeError("No active providers. Run 'xdog-ai login copilot' first.")

    logger.info("Active providers: %s", ", ".join(active))

    # Sync models from upstream APIs (refreshes cache if stale)
    synced = await runtime.sync_models()
    logger.info("Synced %d models from %d provider(s)", len(synced), len(active))

    async def on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_connection(reader, writer, runtime, api_key=api_key)

    server = await asyncio.start_server(on_connect, host, port)
    addr = server.sockets[0].getsockname() if server.sockets else (host, port)
    logger.info("Anthropic API proxy listening on http://%s:%d", addr[0], addr[1])
    print(f"Anthropic API proxy listening on http://{addr[0]}:{addr[1]}")
    print(f"Providers: {', '.join(active)}")
    print(f"Auth: {'API key required' if api_key else 'none (open)'}")
    print("Endpoints:")
    print(f"  POST http://{addr[0]}:{addr[1]}/v1/messages")
    print(f"  GET  http://{addr[0]}:{addr[1]}/v1/models")

    async with server:
        await server.serve_forever()


def run_proxy(
    host: str = "127.0.0.1",
    port: int = 8082,
    api_key: str = "",
) -> None:
    """Run the proxy server (blocking)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(start_proxy(host, port, api_key))
    except KeyboardInterrupt:
        print("\nProxy stopped.")


# ---------------------------------------------------------------------------
# __main__ support: python -m ai.proxy
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Anthropic API proxy backed by the ai package",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8082, help="Port (default: 8082)")
    parser.add_argument("--api-key", default="", help="API key for authentication (default: none)")
    args = parser.parse_args()
    run_proxy(args.host, args.port, args.api_key)
