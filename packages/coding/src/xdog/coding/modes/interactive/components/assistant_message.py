"""Assistant message component for the interactive TUI."""

from __future__ import annotations

from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.markdown import Markdown
from xdog.tui.components.spacer import Spacer
from xdog.tui.components.text import Text
from xdog.tui.tui import Container


class AssistantMessageComponent(Container):
    """Renders a streaming or complete assistant message."""

    def __init__(self, text: str, theme: Theme, *, thinking: str = "") -> None:
        super().__init__()
        self._theme = theme
        self._thinking = Text("", 1, 0)
        self._body = Markdown(text, 1, 0, theme.markdown)
        self.add_child(Spacer(1))
        self.add_child(self._thinking)
        self.add_child(self._body)
        self.set_content(text, thinking=thinking)

    def set_text(self, text: str) -> None:
        """Update response text while preserving current reasoning text."""
        self._body.set_text(text)

    def set_content(self, text: str, *, thinking: str = "") -> None:
        """Update the visible reasoning and final response."""
        reasoning = f"Thinking\n{thinking}" if thinking.strip() else ""
        self._thinking.set_text(self._theme.dim(reasoning))
        self._body.set_text(text)
