"""Tool registry — pure dict. Domains register themselves; registry imports nothing.

Usage::

    from claw.core.tools.registry import register, create_tools
    register("my_tool", my_factory)     # called by domain __init__.py
    tools = create_tools()              # called by GroupRuntime
"""
from __future__ import annotations
from typing import Callable
from agent import AgentTool

ToolFactory = Callable[[], AgentTool]
_registry: dict[str, ToolFactory] = {}


def register(name: str, factory: ToolFactory) -> None:
    """Register a tool factory. Called by domain packages on import."""
    _registry[name] = factory


def create_tools(enabled: tuple[str, ...] = ()) -> list[AgentTool]:
    """Create all registered tools, filtered by enabled set."""
    tools: list[AgentTool] = []
    for name, factory in _registry.items():
        if enabled and name not in enabled:
            continue
        tools.append(factory())
    return tools


def registered_names() -> frozenset[str]:
    return frozenset(_registry.keys())
