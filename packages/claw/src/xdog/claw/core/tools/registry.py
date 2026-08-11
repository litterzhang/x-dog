"""Tool registry — pure dict. Domains register themselves; registry imports nothing.

Usage::

    from xdog.claw.core.tools.registry import register, create_tools
    register("my_tool", my_factory)     # called by domain __init__.py
    tools = create_tools()              # called by GroupRuntime
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from xdog.agent import AgentTool

#: A factory takes no arguments, or an `initial_cwd` when the tool is stateful
#: about where it works — the bash tool is, because `cd` persists across calls.
ToolFactory = Callable[..., AgentTool]
_registry: dict[str, ToolFactory] = {}


def register(name: str, factory: ToolFactory) -> None:
    """Register a tool factory. Called by domain packages on import."""
    _registry[name] = factory


def create_tools(
    enabled: tuple[str, ...] = (), *, workspace_dir: "Path | None" = None
) -> list[AgentTool]:
    """Create all registered tools, filtered by enabled set.

    *workspace_dir* is where the group's agent works. It reaches a factory only
    if the factory asks for `initial_cwd` — the bash tool does, because it holds
    a cwd that `cd` mutates, so it cannot be told per call through the ctx the
    way the filesystem tool is.

    Without this the bash tool defaulted to `Path.cwd()`, which is wherever the
    operator happened to launch the gateway: the group's workspace existed,
    stayed empty, and the agent's shell wrote its files into the launch
    directory instead.
    """
    import inspect

    tools: list[AgentTool] = []
    for name, factory in _registry.items():
        if enabled and name not in enabled:
            continue
        if workspace_dir is not None and "initial_cwd" in inspect.signature(factory).parameters:
            tools.append(factory(initial_cwd=workspace_dir))
        else:
            tools.append(factory())
    return tools


def registered_names() -> frozenset[str]:
    return frozenset(_registry.keys())
