"""Custom editor component for the interactive TUI."""

from __future__ import annotations

from typing import Callable

from xdog.coding.core.slash_commands import list_commands
from xdog.coding.modes.interactive.components.editor_layout import layout_editor
from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.prompt_editor import PromptEditor
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import CURSOR_MARKER
from xdog.tui.utils import char_width

_MAX_INPUT_ROWS = 8


def _layout_input(text: str, width: int) -> tuple[list[str], list[tuple[int, int]]]:
    """Hard-wrap input and map every string offset to a visual row/column."""
    width = max(1, width)
    rows: list[list[str]] = [[]]
    row_widths = [0]
    positions = [(0, 0) for _ in range(len(text) + 1)]
    row = 0

    for index, ch in enumerate(text):
        positions[index] = (row, row_widths[row])
        if ch == "\n":
            rows.append([])
            row_widths.append(0)
            row += 1
            positions[index + 1] = (row, 0)
            continue

        display_width = char_width(ch)
        if row_widths[row] > 0 and row_widths[row] + display_width > width:
            rows.append([])
            row_widths.append(0)
            row += 1
            positions[index] = (row, 0)

        rows[row].append(ch)
        row_widths[row] += display_width
        positions[index + 1] = (row, row_widths[row])

    # A cursor after a completely full final row needs its own display cell.
    if positions[-1][1] >= width:
        rows.append([])
        row_widths.append(0)
        positions[-1] = (len(rows) - 1, 0)

    return ["".join(row_chars) for row_chars in rows], positions


def _insert_at_column(text: str, column: int, marker: str) -> str:
    current = 0
    index = 0
    while index < len(text):
        if text[index] == "\x1b":
            end = text.find("m", index)
            if end >= 0:
                index = end + 1
                continue
        if current >= column:
            return text[:index] + marker + text[index:]
        current += char_width(text[index])
        index += 1
    return text + marker


def _with_cursor(text: str, column: int) -> str:
    """Render a reverse-video cursor at a display-column boundary."""
    current = 0
    for index, ch in enumerate(text):
        if current >= column:
            return text[:index] + f"\x1b[7m{ch}\x1b[27m" + text[index + 1:]
        current += char_width(ch)
    return text + " " * max(0, column - current) + "\x1b[7m \x1b[27m"


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


class CustomEditorComponent(PromptEditor):
    """Input editor with borders, slash command autocomplete, and standard keybindings."""

    def __init__(self, theme: Theme) -> None:
        super().__init__(command_provider=list_commands, max_rows=_MAX_INPUT_ROWS)
        self._theme = theme
        self._select_list: SlashSelectList | None = None

        # Callbacks
        self.on_submit: Callable[[str], None] | None = None
        self.on_escape: Callable[[], None] | None = None
        self.on_ctrl_c: Callable[[], None] | None = None
        self.on_ctrl_d: Callable[[], None] | None = None

    def get_text(self) -> str:
        return self._value

    def set_focus(self, focused: bool) -> None:
        self._focused = focused

    def set_text(self, value: str) -> None:
        self._value = value
        self._cursor = len(value)
        self._preferred_column = None

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
            # `list_commands()`, not BUILTIN_COMMANDS: a skill is a command too,
            # and the dispatcher has always run one. Completing only the built-ins
            # meant an installed skill worked but could not be found -- you had to
            # know its name already, which is the state its own docstring says to
            # avoid.
            matching = [
                (f"/{name}", desc)
                for name, desc in sorted(list_commands().items())
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
        self._render_width = width
        t = self._theme
        lines: list[str] = []

        # Top border
        lines.append(t.border("─" * width))

        # Reserve two columns for the prompt/continuation prefix and hard-wrap
        # by terminal cell width. Terminal autowrap is disabled by TUI, so an
        # overlong physical line would otherwise disappear past the right edge.
        content_width = max(1, width - 2)
        layout = layout_editor(self._value, content_width)
        cursor_row, cursor_col = layout.position(self._cursor)
        first_row = max(0, cursor_row - _MAX_INPUT_ROWS + 1)
        last_row = min(len(layout.rows), first_row + _MAX_INPUT_ROWS)

        for row_index in range(first_row, last_row):
            if row_index == 0:
                prefix = t.bold(t.accent("> "))
            elif row_index == first_row and first_row > 0:
                prefix = t.dim("… ")
            else:
                prefix = "  "

            row_text = layout.rows[row_index].text
            if row_index == cursor_row:
                row_text = _with_cursor(row_text, cursor_col)
                if self._focused:
                    row_text = _insert_at_column(row_text, cursor_col, CURSOR_MARKER)
            lines.append(prefix + row_text)

        # Bottom border
        lines.append(t.border("─" * width))

        # Select list below border
        if self._select_list is not None:
            lines.extend(self._select_list.render(width, t))

        return lines

    def handle_paste(self, text: str) -> bool:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        self._value = self._value[:self._cursor] + normalized + self._value[self._cursor:]
        self._cursor += len(normalized)
        self._preferred_column = None
        self._select_list = None
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        # Alt+Enter works in traditional terminals; Shift+Enter works when the
        # terminal reports modifiers (for example through the Kitty protocol).
        if event.key == "enter" and (event.alt or event.shift):
            self._value = self._value[:self._cursor] + "\n" + self._value[self._cursor:]
            self._cursor += 1
            self._select_list = None
            return True

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

        # Backspace/Delete operate on display clusters, not code points.
        if event.key == "backspace" and self._cursor > 0:
            previous = max(
                boundary for boundary in self._safe_boundaries()
                if boundary < self._cursor
            )
            self._value = self._value[:previous] + self._value[self._cursor:]
            self._cursor = previous
            self._preferred_column = None
            self._update_select_list()
            return True

        if event.key == "delete" and self._cursor < len(self._value):
            following = min(
                boundary for boundary in self._safe_boundaries()
                if boundary > self._cursor
            )
            self._value = self._value[:self._cursor] + self._value[following:]
            self._preferred_column = None
            self._update_select_list()
            return True

        # Movement
        if event.key == "left":
            self._cursor = max(
                (boundary for boundary in self._safe_boundaries() if boundary < self._cursor),
                default=0,
            )
            self._preferred_column = None
            return True
        if event.key == "right":
            self._cursor = min(
                (boundary for boundary in self._safe_boundaries() if boundary > self._cursor),
                default=len(self._value),
            )
            self._preferred_column = None
            return True
        if event.key == "home":
            self._cursor = self._value.rfind("\n", 0, self._cursor) + 1
            return True
        if event.key == "end":
            line_end = self._value.find("\n", self._cursor)
            self._cursor = len(self._value) if line_end < 0 else line_end
            return True
        if event.ctrl and event.key == "a":
            self._cursor = 0
            return True
        if event.ctrl and event.key == "e":
            self._cursor = len(self._value)
            return True

        # Move between explicit input lines. At the outer boundaries, up/down
        # retain their existing history behavior.
        if event.key == "up" and self._move_vertical(-1):
            return True
        if event.key == "down" and self._move_vertical(1):
            return True

        # Kill to the end/start of the current logical line.
        if event.ctrl and event.key == "k":
            line_end = self._value.find("\n", self._cursor)
            if line_end < 0:
                line_end = len(self._value)
            self._value = self._value[:self._cursor] + self._value[line_end:]
            self._update_select_list()
            return True
        if event.ctrl and event.key == "u":
            line_start = self._value.rfind("\n", 0, self._cursor) + 1
            self._value = self._value[:line_start] + self._value[self._cursor:]
            self._cursor = line_start
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

    def _safe_boundaries(self) -> tuple[int, ...]:
        layout = layout_editor(self._value, max(1, self._render_width - 2))
        return tuple(sorted({
            offset
            for row in layout.rows
            for offset, _column in row.boundaries
        }))

    def _move_vertical(self, direction: int) -> bool:
        """Move across wrapped visual rows while preserving display column."""
        content_width = max(1, self._render_width - 2)
        layout = layout_editor(self._value, content_width)
        row_index, column = layout.position(self._cursor)
        target_index = row_index + direction
        if target_index < 0 or target_index >= len(layout.rows):
            self._preferred_column = None
            return False
        if self._preferred_column is None:
            self._preferred_column = column
        self._cursor = layout.rows[target_index].offset_for_column(
            self._preferred_column
        )
        return True

    def _submit(self) -> None:
        """Submit the current input."""
        value = self._value.strip()
        self._value = ""
        self._cursor = 0
        self._hist_idx = -1
        self._select_list = None
        if self.on_submit and value:
            self.on_submit(value)
