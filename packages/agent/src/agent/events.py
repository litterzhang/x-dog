"""Agent lifecycle events — discriminated union of all events emitted by the agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

from ai.types import AssistantMessageEvent, ToolResultMessage

from agent.core import AgentMessage, AgentToolResult


@dataclass(frozen=True)
class AgentStartEvent:
    """Emitted when the agent loop begins."""

    type: Literal["agent_start"] = field(default="agent_start", init=False)


@dataclass(frozen=True)
class AgentEndEvent:
    """Emitted when the agent loop finishes."""

    type: Literal["agent_end"] = field(default="agent_end", init=False)
    messages: tuple[AgentMessage, ...] = ()


@dataclass(frozen=True)
class TurnStartEvent:
    """Emitted at the start of each LLM turn."""

    type: Literal["turn_start"] = field(default="turn_start", init=False)


@dataclass(frozen=True)
class TurnEndEvent:
    """Emitted at the end of each LLM turn."""

    type: Literal["turn_end"] = field(default="turn_end", init=False)
    message: AgentMessage | None = None
    tool_results: tuple[ToolResultMessage, ...] = ()


@dataclass(frozen=True)
class MessageStartEvent:
    """Emitted when a new assistant message begins streaming."""

    type: Literal["message_start"] = field(default="message_start", init=False)
    message: AgentMessage | None = None


@dataclass(frozen=True)
class MessageUpdateEvent:
    """Emitted as the assistant message is incrementally updated."""

    type: Literal["message_update"] = field(default="message_update", init=False)
    message: AgentMessage | None = None
    assistant_message_event: AssistantMessageEvent | None = None


@dataclass(frozen=True)
class MessageEndEvent:
    """Emitted when the assistant message is complete."""

    type: Literal["message_end"] = field(default="message_end", init=False)
    message: AgentMessage | None = None


@dataclass(frozen=True)
class ToolExecutionStartEvent:
    """Emitted when a tool begins executing."""

    type: Literal["tool_execution_start"] = field(default="tool_execution_start", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecutionUpdateEvent:
    """Emitted with incremental tool execution progress."""

    type: Literal["tool_execution_update"] = field(default="tool_execution_update", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    partial_result: AgentToolResult | None = None


@dataclass(frozen=True)
class ToolExecutionEndEvent:
    """Emitted when a tool finishes executing."""

    type: Literal["tool_execution_end"] = field(default="tool_execution_end", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    result: AgentToolResult | None = None
    is_error: bool = False


AgentEvent = Union[
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
]
"""Discriminated union of all agent lifecycle events."""
