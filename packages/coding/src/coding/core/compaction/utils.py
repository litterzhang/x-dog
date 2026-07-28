"""Compaction utilities: token estimation and message splitting."""

from __future__ import annotations

from agent import AgentMessage
from ai.types import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return len(text) // 4


def estimate_message_tokens(msg: AgentMessage) -> int:
    """Estimate the token count of a single message."""
    total = 0

    if isinstance(msg, UserMessage):
        if isinstance(msg.content, str):
            total += estimate_tokens(msg.content)
        else:
            for part in msg.content:
                if isinstance(part, TextContent):
                    total += estimate_tokens(part.text)
                else:
                    total += 200
    elif isinstance(msg, AssistantMessage):
        for part in msg.content:
            if isinstance(part, TextContent):
                total += estimate_tokens(part.text)
            else:
                total += 200
    elif isinstance(msg, ToolResultMessage):
        for part in msg.content:
            if isinstance(part, TextContent):
                total += estimate_tokens(part.text)
            else:
                total += 200
    else:
        total += 200

    # Overhead for role, formatting
    total += 10
    return total


def estimate_history_tokens(messages: list[AgentMessage]) -> int:
    """Estimate total tokens across all messages."""
    return sum(estimate_message_tokens(m) for m in messages)


def split_messages(
    messages: list[AgentMessage],
    target_recent_tokens: int,
) -> tuple[list[AgentMessage], list[AgentMessage]]:
    """Split messages into (old, recent) where recent fits within target tokens.

    Walks backwards from the end, accumulating messages into the "recent"
    bucket until the target is reached.
    """
    recent: list[AgentMessage] = []
    accumulated = 0

    for msg in reversed(messages):
        msg_tokens = estimate_message_tokens(msg)
        if accumulated + msg_tokens > target_recent_tokens and recent:
            break
        recent.append(msg)
        accumulated += msg_tokens

    recent.reverse()
    split_idx = len(messages) - len(recent)
    old = messages[:split_idx]

    return old, recent
