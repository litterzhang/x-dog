"""flow.tools — tool registry and built-in demo tools for workflow nodes."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any

from agent.core import AgentTool, AgentToolResult
from ai.types import TextContent

from flow.errors import WorkflowValidationError


class ToolRegistry:
    """Registry mapping tool names to :class:`~agent.core.AgentTool` objects."""

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        """Register *tool* by its name (replaces any existing entry)."""
        self._tools = {**self._tools, tool.name: tool}

    def get(self, name: str) -> AgentTool:
        """Return the tool registered under *name*.

        Raises :class:`~flow.errors.WorkflowValidationError` if unknown.
        """
        try:
            return self._tools[name]
        except KeyError:
            known = ", ".join(sorted(self._tools)) or "<none>"
            raise WorkflowValidationError(f"Unknown tool {name!r}. Known tools: {known}") from None

    def resolve(self, names: Iterable[str]) -> tuple[AgentTool, ...]:
        """Resolve an iterable of tool names to a tuple of tools."""
        return tuple(self.get(n) for n in names)


# ---------------------------------------------------------------------------
# Built-in demo tool
# ---------------------------------------------------------------------------


async def _echo_execute(
    tool_call_id: str,
    params: dict[str, Any],
    cancel: asyncio.Event | None = None,
    on_update: Any | None = None,
    ctx: dict[str, Any] | None = None,
) -> AgentToolResult:
    text: str = params.get("text", "")
    return AgentToolResult(content=(TextContent(text=text),))


_ECHO_TOOL = AgentTool(
    name="echo",
    description="Echo the given text.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
        },
        "required": ["text"],
    },
    label="Echo",
    execute=_echo_execute,
)


def default_registry() -> ToolRegistry:
    """Return a fresh :class:`ToolRegistry` pre-loaded with built-in tools."""
    registry = ToolRegistry()
    registry.register(_ECHO_TOOL)
    return registry


# ---------------------------------------------------------------------------
# Demo script-node function (referenced as 'flow.tools:passthrough')
# ---------------------------------------------------------------------------


async def passthrough(state: Mapping[str, str]) -> str:
    """Return *state['topic']* or an empty string — demo script-node function."""
    return state.get("topic", "")
