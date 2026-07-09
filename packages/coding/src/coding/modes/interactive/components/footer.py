"""Footer component for the interactive TUI."""

from __future__ import annotations

from tui.components.text import Text

from coding.modes.interactive.theme import Theme, format_tokens


class FooterComponent(Text):
    """Bottom status bar showing model, session, tokens, and working directory."""

    def __init__(self, theme: Theme) -> None:
        super().__init__("", 1, 0)
        self._theme = theme

    def update(
        self,
        *,
        model: str = "unknown",
        session_id: str = "",
        message_count: int = 0,
        thinking: str = "off",
        working_dir: str = "",
        context_tokens: int = 0,
        max_context: int = 200_000,
    ) -> None:
        """Update footer content with current session state."""
        parts: list[str] = []

        if model:
            parts.append(model)
        if thinking and thinking != "off":
            parts.append(f"thinking:{thinking}")
        if session_id:
            parts.append(f"session:{session_id[:8]}")
        if message_count > 0:
            parts.append(f"msgs:{message_count}")
        if context_tokens > 0 and max_context > 0:
            pct = min(100.0, context_tokens / max_context * 100)
            parts.append(f"ctx:{pct:.0f}%/{format_tokens(max_context)}")
        if working_dir:
            # Show last two path components
            segments = working_dir.rstrip("/").split("/")
            short = "/".join(segments[-2:]) if len(segments) > 2 else working_dir
            parts.append(short)

        self.set_text(self._theme.dim(" | ".join(parts)))
