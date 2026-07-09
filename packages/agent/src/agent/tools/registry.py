"""Tool SPI — registry for agent tool factories.

Tools register themselves via :func:`register_tool`. The Agent collects
all registered tools via :func:`get_registered_tools`.

External packages can register tools too::

    from agent.tools.registry import register_tool

    def my_tool_factory(config):
        return AgentTool(name="my_tool", ...)

    register_tool("my_tool", my_tool_factory)
"""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core import AgentTool

ToolFactory = Callable[[Any], "AgentTool | None"]
"""Factory that creates an AgentTool from a config object. Returns None to skip."""

_registry: dict[str, ToolFactory] = {}


def register_tool(name: str, factory: ToolFactory) -> None:
    """Register a tool factory by name."""
    _registry[name] = factory


def unregister_tool(name: str) -> None:
    """Remove a registered tool factory."""
    _registry.pop(name, None)


def get_registered_tools(config: Any = None) -> list[AgentTool]:
    """Instantiate all registered tools. Skips factories that return None."""
    from agent.core import AgentTool  # avoid circular import

    tools: list[AgentTool] = []
    for factory in _registry.values():
        tool = factory(config)
        if tool is not None:
            tools.append(tool)
    return tools


def registered_tool_names() -> frozenset[str]:
    """Return the set of registered tool names."""
    return frozenset(_registry.keys())


def clear_tool_registry() -> None:
    """Remove all registered tools (for testing)."""
    _registry.clear()
