"""Transcript ↔ Message conversion helpers.

Pure functions for converting between JSONL transcript dicts and
``agent.AgentMessage`` types. No class state, no I/O.
"""
from __future__ import annotations

import time
from typing import Any

from ai.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from agent import AgentMessage


def transcript_to_messages(transcript: list[dict]) -> list[AgentMessage]:
    """Convert session transcript dicts to Message types.

    Validates that every ToolResultMessage has a matching ToolCall in the
    preceding AssistantMessage. Orphaned tool results are silently dropped.
    """
    messages: list[AgentMessage] = []
    for turn in transcript:
        role = turn.get("role", "")
        content = turn.get("content", "")

        if role == "user":
            messages.append(UserMessage(content=content))
        elif role == "assistant":
            parts: list[Any] = []
            if content:
                parts.append(TextContent(text=content))
            for tc in turn.get("tool_calls", []):
                parts.append(ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", {}),
                ))
            if parts:
                messages.append(AssistantMessage(content=tuple(parts)))
        elif role == "tool":
            tool_call_id = turn.get("tool_call_id", "")
            if _has_matching_tool_call(messages, tool_call_id):
                messages.append(ToolResultMessage(
                    tool_call_id=tool_call_id,
                    tool_name=turn.get("name", ""),
                    content=(TextContent(text=content),),
                ))
        elif role == "system":
            if content:
                messages.append(UserMessage(content=content))
    return messages


def _has_matching_tool_call(messages: list[AgentMessage], tool_call_id: str) -> bool:
    if not tool_call_id:
        return False
    for msg in reversed(messages):
        if isinstance(msg, AssistantMessage):
            for part in msg.content:
                if isinstance(part, ToolCall) and part.id == tool_call_id:
                    return True
            return False
    return False


def messages_to_transcript(messages: tuple[AgentMessage, ...] | list[AgentMessage]) -> list[dict]:
    """Convert Message types to transcript dicts for JSONL persistence."""
    transcript: list[dict] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                content_str = msg.content
            else:
                content_str = " ".join(
                    p.text for p in msg.content if isinstance(p, TextContent)
                )
            transcript.append({"role": "user", "content": content_str, "timestamp": time.time()})
        elif isinstance(msg, AssistantMessage):
            text_parts = []
            tool_calls = []
            for part in msg.content:
                if isinstance(part, TextContent):
                    text_parts.append(part.text)
                elif isinstance(part, ToolCall):
                    tool_calls.append({
                        "id": part.id,
                        "name": part.name,
                        "arguments": dict(part.arguments) if hasattr(part.arguments, 'items') else part.arguments,
                    })
            entry: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_parts),
                "timestamp": time.time(),
            }
            if tool_calls:
                entry["tool_calls"] = tool_calls
            if msg.usage and msg.usage.total_tokens > 0:
                entry["usage"] = {
                    "input": msg.usage.input,
                    "output": msg.usage.output,
                    "cache_read": msg.usage.cache_read,
                    "cache_write": msg.usage.cache_write,
                }
            transcript.append(entry)
        elif isinstance(msg, ToolResultMessage):
            text = ""
            if msg.content:
                for part in msg.content:
                    if isinstance(part, TextContent):
                        text = part.text
                        break
            transcript.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "name": msg.tool_name,
                "content": text,
                "timestamp": time.time(),
            })
    return transcript


def extract_final_text(
    messages: tuple[AgentMessage, ...],
    previous_count: int = 0,
) -> str:
    """Extract text from all new assistant messages in this turn."""
    new_msgs = messages[previous_count:]
    text_parts: list[str] = []
    for msg in new_msgs:
        if isinstance(msg, AssistantMessage):
            for part in msg.content:
                if isinstance(part, TextContent) and part.text:
                    text_parts.append(part.text)
    return "\n\n".join(text_parts) if text_parts else "(no response)"


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4) if text else 0


def estimate_turn_usage(
    messages: tuple[AgentMessage, ...],
    previous_count: int,
    system_prompt: str,
) -> dict[str, int]:
    """Estimate usage when no real usage data is available."""
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    for msg in messages[previous_count:]:
        if isinstance(msg, AssistantMessage):
            text = ""
            for part in msg.content:
                if isinstance(part, TextContent):
                    text += part.text
            total["output"] += estimate_tokens(text)

    input_chars = len(system_prompt)
    for msg in messages[:previous_count + 1]:
        if isinstance(msg, UserMessage):
            input_chars += len(msg.content) if isinstance(msg.content, str) else 0
        elif isinstance(msg, AssistantMessage):
            for part in msg.content:
                if isinstance(part, TextContent):
                    input_chars += len(part.text)
        elif isinstance(msg, ToolResultMessage):
            for part in msg.content:
                if isinstance(part, TextContent):
                    input_chars += len(part.text)
    total["input"] = max(1, input_chars // 4) if input_chars else 0

    return total
