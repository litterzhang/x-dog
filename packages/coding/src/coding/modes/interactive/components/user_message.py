"""User message component for the interactive TUI."""

from __future__ import annotations

from tui.tui import Container
from tui.components.markdown import Markdown
from tui.components.spacer import Spacer

from coding.modes.interactive.theme import Theme


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
