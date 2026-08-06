"""User message component for the interactive TUI."""

from __future__ import annotations

from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.markdown import Markdown
from xdog.tui.components.spacer import Spacer
from xdog.tui.tui import Container


class UserMessageComponent(Container):
    """Renders a user message with background color styling."""

    def __init__(self, text: str, theme: Theme) -> None:
        super().__init__()
        self.add_child(Spacer(1))
        self.add_child(
            Markdown(
                text, 1, 1,
                theme.markdown,
                default_text_style=theme.user_default_text,
            )
        )
