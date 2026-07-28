"""RPC mode: headless JSON-over-stdio for programmatic access."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from agent import MessageEndEvent, MessageUpdateEvent
from ai.types import AssistantMessage, TextContent, ToolCall

from coding.core.agent_session import AgentSession


async def run_rpc_mode(session: AgentSession) -> int:
    """Run the agent in headless RPC mode.

    Reads JSON commands from stdin (one per line), processes them,
    and emits JSON events on stdout.

    Protocol
    --------
    Input commands (JSON, one per line):
        {"type": "prompt", "message": "..."}
        {"type": "abort"}
        {"type": "compact"}
        {"type": "set_model", "model": "..."}
        {"type": "set_thinking", "level": "..."}
        {"type": "status"}
        {"type": "quit"}

    Output events (JSON, one per line):
        {"type": "message_start"}
        {"type": "text_delta", "text": "..."}
        {"type": "tool_call", "name": "...", "arguments": {...}}
        {"type": "message_end", "content": [...]}
        {"type": "error", "message": "..."}
        {"type": "status", "session_id": "...", "model": "...", ...}
        {"type": "ok"}
        {"type": "quit_ack"}

    Returns 0 on clean exit.
    """
    loop = asyncio.get_running_loop()

    def _emit(event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    _emit({"type": "ready", "session_id": session.session_id})

    while True:
        try:
            raw_line = await loop.run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            break

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

        cmd_type = cmd.get("type", "")

        if cmd_type == "quit":
            _emit({"type": "quit_ack"})
            break

        elif cmd_type == "prompt":
            message = cmd.get("message", "")
            if not message:
                _emit({"type": "error", "message": "Missing 'message' field"})
                continue
            await _handle_prompt(session, message, _emit)

        elif cmd_type == "abort":
            session.abort()
            _emit({"type": "ok", "action": "abort"})

        elif cmd_type == "compact":
            await session.compact()
            _emit({"type": "ok", "action": "compact", "messages": len(session.messages)})

        elif cmd_type == "set_model":
            model_id = cmd.get("model", "")
            if not model_id:
                _emit({"type": "error", "message": "Missing 'model' field"})
                continue
            models = session.agent.state.models
            new_model = models.get(model_id)
            if new_model is None:
                _emit({"type": "error", "message": f"Model not found: {model_id}"})
            else:
                session.set_model(new_model)
                _emit({"type": "ok", "action": "set_model", "model": new_model.id})

        elif cmd_type == "set_thinking":
            level = cmd.get("level", "")
            if level in ("off", ""):
                session.set_thinking_level(None)
                _emit({"type": "ok", "action": "set_thinking", "level": "off"})
            else:
                session.set_thinking_level(level)
                _emit({"type": "ok", "action": "set_thinking", "level": level})

        elif cmd_type == "status":
            model_name = session.model.id if session.model else "unknown"
            thinking = session.agent.state.thinking_level or "off"
            _emit({
                "type": "status",
                "session_id": session.session_id,
                "model": model_name,
                "thinking": str(thinking),
                "messages": len(session.messages),
                "working_dir": str(session.working_dir),
            })

        else:
            _emit({"type": "error", "message": f"Unknown command type: {cmd_type}"})

    return 0


async def _handle_prompt(
    session: AgentSession,
    message: str,
    emit: Any,
) -> None:
    """Handle a prompt command: stream the agent response as JSON events."""
    emit({"type": "message_start"})

    try:
        # Rebuild system prompt before each turn
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

        # Emit final message
        if last_assistant is not None:
            content_parts: list[dict[str, Any]] = []
            for part in last_assistant.content:
                if isinstance(part, TextContent):
                    content_parts.append({"type": "text", "text": part.text})
                elif isinstance(part, ToolCall):
                    content_parts.append({
                        "type": "tool_call",
                        "name": part.name,
                        "arguments": part.arguments,
                    })
            emit({"type": "message_end", "content": content_parts})
        else:
            emit({"type": "message_end", "content": []})

        session._persist()

    except KeyboardInterrupt:
        emit({"type": "error", "message": "Interrupted"})
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
