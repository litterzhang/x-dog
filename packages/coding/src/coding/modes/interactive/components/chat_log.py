"""Chat log component for the interactive TUI."""

from __future__ import annotations

from tui.tui import Component, Container
from tui.components.text import Text

from coding.modes.interactive.theme import Theme
from coding.modes.interactive.components.assistant_message import AssistantMessageComponent
from coding.modes.interactive.components.user_message import UserMessageComponent
from coding.modes.interactive.components.tool_execution import ToolExecutionComponent


class ChatLog(Container):
    """Scrollable chat log with streaming support and component pruning."""

    MAX_COMPONENTS = 200

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self._theme = theme
        self._streaming: dict[str, AssistantMessageComponent] = {}
        self._last_tool: ToolExecutionComponent | None = None

    def add_user(self, text: str) -> None:
        """Add a user message."""
        self._append(UserMessageComponent(text, self._theme))

    def add_assistant(self, text: str) -> None:
        """Add a completed assistant message."""
        self._append(AssistantMessageComponent(text, self._theme))

    def add_system(self, text: str) -> None:
        """Add a system/info message."""
        self._append(Text(self._theme.system(f"  {text}"), 1, 0))

    def add_tool(self, tool_name: str, arguments: dict | None = None) -> ToolExecutionComponent:
        """Add a tool execution component."""
        comp = ToolExecutionComponent(tool_name, arguments, self._theme)
        self._last_tool = comp
        self._append(comp)
        return comp

    def get_last_tool(self) -> ToolExecutionComponent | None:
        """Return the most recently added tool execution component."""
        return self._last_tool

    def start_assistant(self, text: str, stream_id: str = "default") -> AssistantMessageComponent:
        """Start a new streaming assistant message."""
        existing = self._streaming.get(stream_id)
        if existing is not None:
            existing.set_text(text)
            return existing
        comp = AssistantMessageComponent(text, self._theme)
        self._streaming[stream_id] = comp
        self._append(comp)
        return comp

    def update_assistant(self, text: str, stream_id: str = "default") -> None:
        """Update an existing streaming assistant message."""
        existing = self._streaming.get(stream_id)
        if existing is None:
            self.start_assistant(text, stream_id)
            return
        existing.set_text(text)

    def finalize_assistant(self, text: str, stream_id: str = "default") -> None:
        """Finalize a streaming assistant message."""
        existing = self._streaming.get(stream_id)
        if existing is not None:
            existing.set_text(text)
            del self._streaming[stream_id]
            return
        self._append(AssistantMessageComponent(text, self._theme))

    def drop_assistant(self, stream_id: str = "default") -> None:
        """Remove a streaming assistant component (e.g. on abort)."""
        existing = self._streaming.get(stream_id)
        if existing is None:
            return
        self.remove_child(existing)
        del self._streaming[stream_id]

    def clear_all(self) -> None:
        """Clear all messages."""
        self.clear()
        self._streaming.clear()

    def _append(self, comp: Component) -> None:
        self.add_child(comp)
        self._prune()

    def _prune(self) -> None:
        while len(self.children) > self.MAX_COMPONENTS:
            oldest = self.children[0]
            self.children.pop(0)
            # Clean up streaming references
            for sid, msg in list(self._streaming.items()):
                if msg is oldest:
                    del self._streaming[sid]
