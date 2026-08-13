"""RPC mode: headless JSON-over-stdio for programmatic access."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any, cast

from xdog.agent import MessageEndEvent, MessageUpdateEvent
from xdog.ai.types import AssistantMessage, TextContent, ThinkingContent, ToolCall
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionDecision, PermissionRequest

EmitFn = Callable[[dict[str, Any]], None]


async def run_rpc_mode(session: AgentSession) -> int:
    """Run the JSON-lines RPC protocol, including asynchronous approvals.

    Prompt turns run in a task while this coroutine continues reading commands.
    That is required for a client to answer ``permission_request`` while the
    corresponding tool hook is suspended.
    """
    loop = asyncio.get_running_loop()

    def _emit(event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def _permission_request(request: PermissionRequest) -> None:
        _emit({
            "type": "permission_request",
            "id": request.id,
            "tool": request.tool_name,
            "arguments": request.arguments,
            "summary": request.summary,
        })

    session.permissions.set_request_handler(_permission_request)
    _emit({
        "type": "ready",
        "session_id": session.session_id,
        "permission_mode": session.permissions.mode,
    })

    turn_task: asyncio.Task[None] | None = None
    quitting = False
    try:
        while True:
            try:
                raw_line = await loop.run_in_executor(None, sys.stdin.readline)
            except (EOFError, KeyboardInterrupt):
                break

            if turn_task is not None and turn_task.done():
                await turn_task
                turn_task = None

            if not raw_line:
                break
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            try:
                cmd = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                _emit({"type": "error", "message": f"Invalid JSON: {exc}"})
                continue
            if not isinstance(cmd, dict):
                _emit({"type": "error", "message": "RPC command must be an object"})
                continue

            cmd_type = cmd.get("type", "")
            turn_running = turn_task is not None and not turn_task.done()

            if cmd_type == "quit":
                quitting = True
                session.abort()
                session.permissions.deny_all()
                _emit({"type": "quit_ack"})
                break

            if cmd_type == "permission_response":
                request_id = cmd.get("id", "")
                decision = cmd.get("decision", "")
                valid = ("allow_once", "allow_session", "deny")
                if not isinstance(request_id, str) or decision not in valid:
                    _emit({"type": "error", "message": "Invalid permission response"})
                    continue
                resolved = session.permissions.resolve(
                    request_id,
                    cast(PermissionDecision, decision),
                )
                if resolved:
                    _emit({
                        "type": "ok",
                        "action": "permission_response",
                        "id": request_id,
                    })
                else:
                    _emit({
                        "type": "error",
                        "message": f"Unknown permission request: {request_id}",
                    })
                continue

            if cmd_type == "prompt":
                message = cmd.get("message", "")
                if not isinstance(message, str) or not message:
                    _emit({"type": "error", "message": "Missing 'message' field"})
                elif turn_running:
                    _emit({"type": "error", "message": "A prompt is already running"})
                else:
                    turn_task = asyncio.create_task(_handle_prompt(session, message, _emit))
                continue

            if cmd_type == "abort":
                session.abort()
                session.permissions.deny_all()
                _emit({"type": "ok", "action": "abort"})
                continue

            if cmd_type in ("compact", "set_model", "set_thinking") and turn_running:
                _emit({"type": "error", "message": f"Cannot {cmd_type} during a running prompt"})
                continue

            if cmd_type == "compact":
                await session.compact()
                _emit({"type": "ok", "action": "compact", "messages": len(session.messages)})
            elif cmd_type == "set_model":
                model_id = cmd.get("model", "")
                if not isinstance(model_id, str) or not model_id:
                    _emit({"type": "error", "message": "Missing 'model' field"})
                    continue
                session.set_model(model_id)
                _emit({"type": "ok", "action": "set_model", "model": model_id})
            elif cmd_type == "set_thinking":
                level = cmd.get("level", "")
                if level in ("off", ""):
                    session.set_thinking_level(None)
                    _emit({"type": "ok", "action": "set_thinking", "level": "off"})
                elif level in ("minimal", "low", "medium", "high", "xhigh"):
                    session.set_thinking_level(cast(Any, level))
                    _emit({"type": "ok", "action": "set_thinking", "level": level})
                else:
                    _emit({"type": "error", "message": f"Invalid thinking level: {level}"})
            elif cmd_type == "status":
                thinking = session.agent.options.thinking or "off"
                _emit({
                    "type": "status",
                    "session_id": session.session_id,
                    "model": session.model or "unknown",
                    "thinking": str(thinking),
                    "permission_mode": session.permissions.mode,
                    "prompt_running": turn_running,
                    "messages": len(session.messages),
                    "working_dir": str(session.working_dir),
                })
            else:
                _emit({"type": "error", "message": f"Unknown command type: {cmd_type}"})
    finally:
        session.permissions.set_request_handler(None)
        session.permissions.deny_all()
        if turn_task is not None and not turn_task.done():
            if quitting:
                turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)

    return 0


async def _handle_prompt(
    session: AgentSession,
    message: str,
    emit: EmitFn,
) -> None:
    """Handle one prompt while the RPC command reader remains active."""
    emit({"type": "message_start"})

    try:
        session._rebuild_system_prompt()
        await session._maybe_compact()
        event_stream = await session.agent.prompt(message)

        last_assistant: AssistantMessage | None = None
        async for event in event_stream:
            if isinstance(event, MessageUpdateEvent):
                msg = event.message
                if isinstance(msg, AssistantMessage):
                    for part in msg.content:
                        if isinstance(part, TextContent):
                            emit({"type": "text_delta", "text": part.text})
                        elif isinstance(part, ThinkingContent) and not part.redacted:
                            emit({"type": "thinking_delta", "text": part.thinking})
                        elif isinstance(part, ToolCall):
                            emit({
                                "type": "tool_call",
                                "name": part.name,
                                "arguments": part.arguments,
                            })
            elif isinstance(event, MessageEndEvent):
                msg = event.message
                if isinstance(msg, AssistantMessage):
                    last_assistant = msg

        content_parts: list[dict[str, Any]] = []
        if last_assistant is not None:
            for part in last_assistant.content:
                if isinstance(part, TextContent):
                    content_parts.append({"type": "text", "text": part.text})
                elif isinstance(part, ThinkingContent) and not part.redacted:
                    content_parts.append({"type": "thinking", "thinking": part.thinking})
                elif isinstance(part, ToolCall):
                    content_parts.append({
                        "type": "tool_call",
                        "name": part.name,
                        "arguments": part.arguments,
                    })
        emit({"type": "message_end", "content": content_parts})
        session._persist()
    except asyncio.CancelledError:
        emit({"type": "error", "message": "Interrupted"})
        raise
    except KeyboardInterrupt:
        emit({"type": "error", "message": "Interrupted"})
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
