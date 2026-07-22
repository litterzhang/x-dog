"""Fixture custom tools for the flow tool-manifest tests.

``make_reverse`` is a zero-arg factory; ``REVERSE_TOOL`` is a module-level
``AgentTool`` constant.  Both deliberately use internal names different from the
manifest keys, so tests can assert the manifest name wins.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent.core import AgentTool, AgentToolResult
from ai.types import TextContent

_PARAMS = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


async def _reverse_exec(
    tool_call_id: str,
    params: dict[str, Any],
    cancel: asyncio.Event | None = None,
    on_update: Any | None = None,
    ctx: dict[str, Any] | None = None,
) -> AgentToolResult:
    return AgentToolResult(content=(TextContent(text=str(params.get("text", ""))[::-1]),))


def make_reverse() -> AgentTool:
    """Factory form: returns a fresh reverse tool with an internal name."""
    return AgentTool(
        name="reverse_internal",
        description="Reverse the given text.",
        parameters=_PARAMS,
        execute=_reverse_exec,
    )


REVERSE_TOOL = AgentTool(
    name="reverse_const_internal",
    description="Reverse the given text (constant form).",
    parameters=_PARAMS,
    execute=_reverse_exec,
)


NOT_A_TOOL = 42
