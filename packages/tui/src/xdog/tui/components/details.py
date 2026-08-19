"""Shared detail expansion support for retained component trees."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from xdog.tui.tui import Component, Container


@runtime_checkable
class ExpandableComponent(Protocol):
    """Component whose detailed presentation can be expanded or collapsed."""

    def set_expanded(self, expanded: bool) -> None:
        """Select the expanded or compact presentation."""


def set_details_expanded(component: Component, expanded: bool) -> None:
    """Apply a detail mode recursively without changing component identity."""
    if isinstance(component, ExpandableComponent):
        component.set_expanded(expanded)
    if isinstance(component, Container):
        for child in tuple(component.children):
            set_details_expanded(child, expanded)
