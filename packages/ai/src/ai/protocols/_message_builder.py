"""Shared mutable message builder for streaming protocols.

Accumulates streaming deltas into an immutable :class:`AssistantMessage`
snapshot.  All three protocol implementations use this class to track
content blocks, usage, and metadata during a streaming response.

Uses a dirty-flag cache so repeated ``snapshot()`` calls without
intervening mutations return the same frozen object.
"""

from __future__ import annotations

import time
from typing import Any

from ai.types import (
    AssistantContentPart,
    AssistantMessage,
    Model,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)


class MessageBuilder:
    """Accumulates streaming deltas into an AssistantMessage.

    This is intentionally mutable during streaming.  Call :meth:`snapshot`
    to obtain a frozen :class:`AssistantMessage` suitable for event payloads.
    """

    def __init__(self, model: Model) -> None:
        self.content: list[dict[str, Any]] = []
        self.api: str = model.api
        self.provider: str = model.provider
        self.model_id: str = model.id
        self.response_id: str | None = None
        self.usage: Usage = Usage()
        self.stop_reason: StopReason = "stop"
        self.error_message: str | None = None
        self.timestamp: float = time.time()
        self._dirty: bool = True
        self._cached_snapshot: AssistantMessage | None = None

    @property
    def block_index(self) -> int:
        """Index of the last content block (-1 when empty)."""
        return len(self.content) - 1

    def push_block(self, block: dict[str, Any]) -> int:
        """Append a new content block and return its index."""
        self.content.append(block)
        self._dirty = True
        return self.block_index

    def current_block(self) -> dict[str, Any] | None:
        """Return the last content block, or ``None`` if empty."""
        return self.content[-1] if self.content else None

    def mark_dirty(self) -> None:
        """Mark builder as dirty so next ``snapshot()`` rebuilds."""
        self._dirty = True

    def _freeze_content(self) -> tuple[AssistantContentPart, ...]:
        """Convert the mutable content list to an immutable tuple."""
        parts: list[AssistantContentPart] = []
        for block in self.content:
            btype = block.get("type")
            if btype == "text":
                parts.append(TextContent(
                    text=block.get("text", ""),
                    text_signature=block.get("text_signature"),
                ))
            elif btype == "thinking":
                parts.append(ThinkingContent(
                    thinking=block.get("thinking", ""),
                    thinking_signature=block.get("thinking_signature"),
                    redacted=block.get("redacted", False),
                ))
            elif btype == "toolCall":
                parts.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("arguments") or {},
                    thought_signature=block.get("thought_signature"),
                ))
        return tuple(parts)

    def snapshot(self) -> AssistantMessage:
        """Return a frozen immutable snapshot of the message being built.

        Caches the result until the builder is mutated again (dirty flag).
        """
        if not self._dirty and self._cached_snapshot is not None:
            return self._cached_snapshot
        self._cached_snapshot = AssistantMessage(
            content=self._freeze_content(),
            api=self.api,
            provider=self.provider,
            model=self.model_id,
            response_id=self.response_id,
            usage=self.usage,
            stop_reason=self.stop_reason,
            error_message=self.error_message,
            timestamp=self.timestamp,
        )
        self._dirty = False
        return self._cached_snapshot
