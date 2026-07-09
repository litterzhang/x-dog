"""Custom editor component for the interactive TUI."""

from __future__ import annotations

from typing import Any, Callable

from tui.tui import Component
from tui.keys import KeyEvent

from coding.modes.interactive.theme import Theme
from coding.core.slash_commands import BUILTIN_COMMANDS


class SlashSelectList:
    """Minimal select list for slash command autocomplete."""

    def __init__(self, items: list[tuple[str, str]], max_visible: int = 5) -> None:
        self._items = items
        self._selected = 0
        self._max_visible = max_visible

    @property
    def selected_value(self) -> str | None:
        if 0 <= self._selected < len(self._items):
            return self._items[self._selected][0]
        return None

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self._items = items
        self._selected = min(self._selected, max(0, len(items) - 1))

    def move(self, delta: int) -> None:
        if not self._items:
            return
        self._selected = (self._selected + delta) % len(self._items)

    def render(self, width: int, theme: Theme) -> list[str]:
        if not self._items:
            return [theme.dim("  No matching commands")]
        lines: list[str] = []
        n = len(self._items)
        start = max(0, min(self._selected - self._max_visible // 2, n - self._max_visible))
        end = min(start + self._max_visible, n)
        for i in range(start, end):
            value, desc = self._items[i]
            if i == self._selected:
                lines.append(theme.accent(f"→ {value:<14s} {desc}"))
            else:
                lines.append(f"  {value:<14s} {theme.dim(desc)}")
        if start > 0 or end < n:
            lines.append(theme.dim(f"  ({self._selected + 1}/{n})"))
        return lines


class CustomEditorComponent(Component):
    """Input editor with borders, slash command autocomplete, and standard keybindings."""

    def __init__(self, theme: Theme) -> None:
        self._theme = theme
        self._value = ""
        self._cursor = 0
        self._history: list[str] = []
        self._hist_idx = -1
        self._hist_stash = ""
        self._select_list: SlashSelectList | None = None

        # Callbacks
        self.on_submit: Callable[[str], None] | None = None
        self.on_escape: Callable[[], None] | None = None
        self.on_ctrl_c: Callable[[], None] | None = None
        self.on_ctrl_d: Callable[[], None] | None = None

    def get_text(self) -> str:
        return self._value

    def set_text(self, value: str) -> None:
        self._value = value
        self._cursor = len(value)

    def add_to_history(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._hist_idx = -1

    def invalidate(self) -> None:
        pass

    def _update_select_list(self) -> None:
        """Update the select list based on current input."""
        if self._value.startswith("/"):
            prefix = self._value[1:]
            matching = [
                (f"/{name}", desc)
                for name, desc in BUILTIN_COMMANDS.items()
                if name.startswith(prefix) and f"/{name}" != self._value
            ]
            if matching:
                if self._select_list is None:
                    self._select_list = SlashSelectList(matching)
                else:
                    self._select_list.set_items(matching)
            else:
                self._select_list = None
        else:
            self._select_list = None

    def render(self, width: int) -> list[str]:
        t = self._theme
        lines: list[str] = []

        # Top border
        lines.append(t.border("─" * width))

        # Input line with cursor
        prompt = t.bold(t.accent("> "))
        before = self._value[:self._cursor]
        after = self._value[self._cursor:]
        cursor_ch = after[0] if after else " "
        rest = after[1:] if after else ""
        lines.append(prompt + before + f"\x1b[7m{cursor_ch}\x1b[27m" + rest)

        # Bottom border
        lines.append(t.border("─" * width))

        # Select list below border
        if self._select_list is not None:
            lines.extend(self._select_list.render(width, t))

        return lines

    def handle_input(self, event: KeyEvent) -> bool:
        # Escape
        if event.key == "escape":
            if self._select_list is not None:
                self._select_list = None
                return True
            if self.on_escape:
                self.on_escape()
            return True

        # Ctrl+C
        if event.ctrl and event.key == "c" and self.on_ctrl_c:
            self.on_ctrl_c()
            return True

        # Ctrl+D
        if event.ctrl and event.key == "d":
            if len(self._value) == 0 and self.on_ctrl_d:
                self.on_ctrl_d()
            return True

        # Select list navigation
        if self._select_list is not None:
            if event.key == "up":
                self._select_list.move(-1)
                return True
            if event.key == "down":
                self._select_list.move(1)
                return True
            if event.key in ("tab", "enter"):
                selected = self._select_list.selected_value
                if selected is not None:
                    self._value = selected
                    self._cursor = len(self._value)
                    self._select_list = None
                    if event.key == "enter":
                        self._submit()
                    else:
                        self._update_select_list()
                return True

        # Enter
        if event.key == "enter":
            self._submit()
            return True

        # Backspace
        if event.key == "backspace" and self._cursor > 0:
            self._value = self._value[:self._cursor - 1] + self._value[self._cursor:]
            self._cursor -= 1
            self._update_select_list()
            return True

        # Delete
        if event.key == "delete" and self._cursor < len(self._value):
            self._value = self._value[:self._cursor] + self._value[self._cursor + 1:]
            self._update_select_list()
            return True

        # Movement
        if event.key == "left":
            self._cursor = max(0, self._cursor - 1)
            return True
        if event.key == "right":
            self._cursor = min(len(self._value), self._cursor + 1)
            return True
        if event.key == "home" or (event.ctrl and event.key == "a"):
            self._cursor = 0
            return True
        if event.key == "end" or (event.ctrl and event.key == "e"):
            self._cursor = len(self._value)
            return True

        # Kill line
        if event.ctrl and event.key == "k":
            self._value = self._value[:self._cursor]
            self._update_select_list()
            return True
        if event.ctrl and event.key == "u":
            self._value = self._value[self._cursor:]
            self._cursor = 0
            self._update_select_list()
            return True

        # History (only when no select list)
        if event.key == "up" and self._history:
            if self._hist_idx == -1:
                self._hist_stash = self._value
                self._hist_idx = len(self._history) - 1
            elif self._hist_idx > 0:
                self._hist_idx -= 1
            else:
                return True
            self._value = self._history[self._hist_idx]
            self._cursor = len(self._value)
            self._update_select_list()
            return True
        if event.key == "down" and self._hist_idx >= 0:
            if self._hist_idx < len(self._history) - 1:
                self._hist_idx += 1
                self._value = self._history[self._hist_idx]
            else:
                self._hist_idx = -1
                self._value = self._hist_stash
            self._cursor = len(self._value)
            self._update_select_list()
            return True

        # Printable characters
        if len(event.key) == 1 and not event.ctrl and not event.alt:
            self._value = self._value[:self._cursor] + event.key + self._value[self._cursor:]
            self._cursor += 1
            self._update_select_list()
            return True

        return False

    def _submit(self) -> None:
        """Submit the current input."""
        value = self._value.strip()
        self._value = ""
        self._cursor = 0
        self._hist_idx = -1
        self._select_list = None
        if self.on_submit and value:
            self.on_submit(value)
