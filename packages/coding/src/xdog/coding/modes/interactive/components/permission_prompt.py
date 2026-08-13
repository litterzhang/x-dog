"""Interactive tool-permission approval panel."""

from __future__ import annotations

from collections.abc import Callable

from xdog.coding.core.permissions import PermissionDecision, PermissionRequest
from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.box import Box
from xdog.tui.components.select_list import SelectItem, SelectList
from xdog.tui.components.text import Text
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component


class PermissionPromptComponent(Component):
    """Focused inline panel that resolves one permission request."""

    def __init__(
        self,
        request: PermissionRequest,
        theme: Theme,
        on_decision: Callable[[PermissionDecision], None],
    ) -> None:
        self.request = request
        self._on_decision = on_decision
        self._box = Box(padding_x=2, padding_y=1)
        self._box.add_child(Text(theme.bold("Tool permission required"), 0, 0))
        self._box.add_child(Text(request.summary, 0, 1))

        self._choices: SelectList[PermissionDecision] = SelectList(
            [
                SelectItem("Allow once", "allow_once", "Run only this call"),
                SelectItem(
                    "Allow for this session",
                    "allow_session",
                    "Remember this exact call",
                ),
                SelectItem("Deny", "deny", "Return a denial to the model"),
            ],
            max_visible=3,
            selected_fn=theme.accent,
            description_fn=theme.dim,
            scroll_fn=theme.dim,
        )
        self._choices.on_select_cb = lambda item: self._on_decision(item.value)
        self._choices.on_cancel_cb = lambda: self._on_decision("deny")
        self._box.add_child(self._choices)
        self._box.add_child(Text(theme.dim("Enter to select • Esc to deny"), 0, 1))

    def render(self, width: int) -> list[str]:
        return self._box.render(width)

    def handle_input(self, event: KeyEvent) -> bool:
        return self._choices.handle_input(event)

    def invalidate(self) -> None:
        self._box.invalidate()
