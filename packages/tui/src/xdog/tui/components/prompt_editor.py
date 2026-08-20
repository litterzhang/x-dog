"""Shared prompt-editor base for coding and assistant TUIs."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from xdog.tui.editor_layout import EditorLayout, layout_editor
from xdog.tui.tui import CURSOR_MARKER, Component


class PromptEditor(Component):
    """Shared text/history/paste/layout state; applications provide styling."""

    def __init__(
        self,
        *,
        command_provider: Callable[[], Mapping[str, str]] | None = None,
        max_rows: int = 8,
    ) -> None:
        self._value = ""
        self._cursor = 0
        self._history: list[str] = []
        self._hist_idx = -1
        self._hist_stash = ""
        self._render_width = 80
        self._preferred_column: int | None = None
        self._focused = False
        self._command_provider = command_provider or (lambda: {})
        self._max_rows = max_rows

    def get_text(self) -> str:
        return self._value

    def set_text(self, value: str) -> None:
        self._value = value
        self._cursor = len(value)
        self._preferred_column = None

    def set_focus(self, focused: bool) -> None:
        self._focused = focused

    def add_to_history(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._hist_idx = -1

    def handle_paste(self, text: str) -> bool:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        self._value = self._value[:self._cursor] + normalized + self._value[self._cursor:]
        self._cursor += len(normalized)
        self._preferred_column = None
        return True

    def layout(self, width: int) -> EditorLayout:
        self._render_width = width
        return layout_editor(self._value, max(1, width - 2))

    def cursor_marker(self) -> str:
        return CURSOR_MARKER if self._focused else ""

    def render(self, width: int) -> list[str]:
        layout = self.layout(width)
        return [row.text for row in layout.rows[-self._max_rows:]]
