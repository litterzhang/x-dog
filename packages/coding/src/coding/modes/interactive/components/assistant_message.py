"""Assistant message component for the interactive TUI."""

from __future__ import annotations

from tui.tui import Container
from tui.components.markdown import Markdown
from tui.components.spacer import Spacer

from coding.modes.interactive.theme import Theme


class AssistantMessageComponent(Container):
    """Renders a streaming or complete assistant message."""

    def __init__(self, text: str, theme: Theme) -> None:
        super().__init__()
        self._body = Markdown(text, 1, 0, theme.markdown)
        self.add_child(Spacer(1))
        self.add_child(self._body)

    def set_text(self, text: str) -> None:
        """Update the message text (used during streaming)."""
        self._body.set_text(text)
