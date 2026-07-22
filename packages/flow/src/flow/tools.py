"""flow.tools — tool registry and built-in demo tools for workflow nodes."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import sys
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.core import AgentTool, AgentToolResult
from ai.types import TextContent

from flow.errors import WorkflowValidationError

if TYPE_CHECKING:
    from flow.models import WorkflowDef


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

    def names(self) -> frozenset[str]:
        """Return the set of registered tool names."""
        return frozenset(self._tools)

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
    """Return a fresh :class:`ToolRegistry` pre-loaded with built-in tools.

    Includes the demo ``echo`` tool plus every agent-package builtin registered
    via ``agent.tools`` (``bash``, ``filesystem``, ``current_time``,
    ``submit_result``, …).  This means a workflow node can declare
    ``"tools": ["filesystem"]`` and have it resolve out of the box — including
    from the ``xdog-flow run`` CLI, which uses this registry by default.
    """
    registry = ToolRegistry()
    registry.register(_ECHO_TOOL)
    for tool in _agent_builtin_tools():
        registry.register(tool)
    return registry


def _agent_builtin_tools() -> tuple[AgentTool, ...]:
    """Return the agent-package builtin tools, or empty if unavailable.

    Imported lazily so ``flow.tools`` has no import-time dependency on the agent
    tool registry being populated.
    """
    try:
        from agent.tools.registry import get_registered_tools
    except ImportError:  # pragma: no cover - agent always present in the workspace
        return ()
    return tuple(get_registered_tools())


# ---------------------------------------------------------------------------
# Custom tool manifest loading (workflow-level "tools" refs)
# ---------------------------------------------------------------------------


def coerce_tool(obj: object) -> AgentTool:
    """Coerce a loaded manifest reference into an :class:`AgentTool`.

    Accepts an :class:`AgentTool` instance as-is, or a zero-argument callable
    (a factory) whose return value is an :class:`AgentTool`.
    """
    if isinstance(obj, AgentTool):
        return obj
    if callable(obj):
        built = obj()
        if not isinstance(built, AgentTool):
            raise WorkflowValidationError(
                f"Tool factory {getattr(obj, '__name__', obj)!r} must return an AgentTool, got {type(built).__name__}"
            )
        return built
    raise WorkflowValidationError(
        f"Tool ref must resolve to an AgentTool or a factory returning one, got {type(obj).__name__}"
    )


def load_tool_ref(ref: str, base_dir: Path | None = None) -> AgentTool:
    """Load an ``AgentTool`` from a ``'module:attr'`` reference.

    Imports *module* with *base_dir* (the workflow's own directory) temporarily
    on ``sys.path`` — the same idiom script nodes use for ``run`` — so a workflow
    can bundle its tool ``.py`` files alongside the JSON.  The resolved attribute
    is coerced via :func:`coerce_tool`.
    """
    module_name, _, attr = ref.partition(":")
    if base_dir is not None:
        sys.path.insert(0, str(base_dir))
        importlib.invalidate_caches()
        try:
            module = importlib.import_module(module_name)
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(base_dir))
    else:
        module = importlib.import_module(module_name)
    return coerce_tool(getattr(module, attr))


def register_workflow_tools(wf: WorkflowDef, registry: ToolRegistry, base_dir: Path | None = None) -> None:
    """Load every tool in *wf*'s manifest and register it into *registry*.

    Each ``(name, ref)`` pair is loaded via :func:`load_tool_ref` and registered
    under the manifest *name* (authoritative, so the LLM-facing ``tool.name``
    matches what nodes reference), overriding the tool's own name if different.
    """
    for name, ref in wf.tool_refs:
        tool = load_tool_ref(ref, base_dir)
        registry.register(replace(tool, name=name))


def bind_tool(obj: object, name: str) -> AgentTool:
    """Coerce *obj* to an :class:`AgentTool` and rename it to *name*.

    The single helper generated workflow modules call to register a manifest
    tool under its authoritative name (keeps their import list to one
    ``flow.tools`` line — no ``dataclasses`` import needed).
    """
    return replace(coerce_tool(obj), name=name)
