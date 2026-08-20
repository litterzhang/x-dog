"""Registry for application-specific tool presentation hooks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xdog.tui.tui import Component

ToolRenderer = Callable[[str, Any], Component | None]

_RENDERERS: dict[str, ToolRenderer] = {}


def register_tool_renderer(tool_name: str, renderer: ToolRenderer) -> None:
    _RENDERERS[tool_name] = renderer


def get_tool_renderer(tool_name: str) -> ToolRenderer | None:
    return _RENDERERS.get(tool_name)


def unregister_tool_renderer(tool_name: str) -> None:
    _RENDERERS.pop(tool_name, None)
