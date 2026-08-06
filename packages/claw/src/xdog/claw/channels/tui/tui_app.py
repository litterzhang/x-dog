"""OpenClaw-style TUI for claw — faithful port of openclaw/src/tui.

Uses the same architecture as OpenClaw and the original TypeScript pi-tui:
- Components return ``list[str]`` (ANSI-styled lines)
- Container concatenates children's lines
- TUI diffs string arrays for differential rendering

Component tree (matching OpenClaw exactly):
    TUI
    └── root (Container)
        ├── header (Text)
        ├── chatLog (ChatLog extends Container)
        │   ├── UserMessage (Container: Spacer + Markdown with bg/color)
        │   ├── AssistantMessage (Container: Spacer + Markdown, default fg)
        │   └── SystemMessage (Spacer + Text)
        ├── statusContainer (Container: swaps Loader / Text)
        ├── footer (Text)
        └── editor (CustomEditor)

Event protocol (matching OpenClaw's chat/agent events):
    delta   → streaming tokens (updateAssistant)
    final   → completed response (finalizeAssistant)
    aborted → run was cancelled
    error   → run errored
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import random
import threading
import time
from pathlib import Path
from typing import Any

from xdog.tui.components.loader import Loader
from xdog.tui.components.markdown import DefaultTextStyle, Markdown, MarkdownTheme
from xdog.tui.components.spacer import Spacer
from xdog.tui.components.text import Text
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import TUI, Component, Container

logger = logging.getLogger(__name__)

# ── ANSI color helpers (matching OpenClaw's chalk usage) ──────────────

_RST = "\x1b[0m"


def _fg(hex_color: str) -> "callable":
    """Return a function that applies foreground color.

    Uses \\x1b[39m (fg-only reset) instead of \\x1b[0m (full reset),
    matching OpenClaw's theme.fg() which does NOT kill background color.
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    prefix = f"\x1b[38;2;{r};{g};{b}m"

    def apply(text: str) -> str:
        return f"{prefix}{text}\x1b[39m"

    return apply


def _bg(hex_color: str) -> "callable":
    """Return a function that applies background color.

    Uses \\x1b[49m (bg-only reset) instead of \\x1b[0m (full reset),
    matching OpenClaw's theme.bg() which does NOT kill foreground color.
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    prefix = f"\x1b[48;2;{r};{g};{b}m"

    def apply(text: str) -> str:
        return f"{prefix}{text}\x1b[49m"

    return apply


def _bold(text: str) -> str:
    return f"\x1b[1m{text}{_RST}"


def _dim(text: str) -> str:
    return f"\x1b[2m{text}{_RST}"


def _italic(text: str) -> str:
    return f"\x1b[3m{text}{_RST}"


def _context_usage_tokens(
    last_turn: dict[str, int],
    session_total: dict[str, int],
) -> int:
    """Tokens currently occupying the model's context window.

    Cached prefix tokens still occupy the window, so the most recent turn's
    ``input + cache_read + cache_write`` is the real occupancy. Session totals
    are cumulative across turns and would over-count, so they are only used as
    a fallback before the first turn of a (re)connected session completes.
    """
    turn_total = (
        last_turn.get("input", 0)
        + last_turn.get("cache_read", 0)
        + last_turn.get("cache_write", 0)
    )
    if turn_total > 0:
        return turn_total
    return session_total.get("input", 0)


def _format_tokens(count: int) -> str:
    """Format token count like OpenClaw's formatTokens().

    < 1,000:       raw number (e.g. "500")
    < 10,000:      one decimal (e.g. "5.2k")
    < 1,000,000:   rounded thousands (e.g. "150k")
    < 10,000,000:  one decimal millions (e.g. "5.2M")
    >= 10,000,000: rounded millions (e.g. "12M")
    """
    if count < 1_000:
        return str(count)
    if count < 10_000:
        return f"{count / 1_000:.1f}k"
    if count < 1_000_000:
        return f"{count // 1_000}k"
    if count < 10_000_000:
        return f"{count / 1_000_000:.1f}M"
    return f"{count // 1_000_000}M"


# ── OpenClaw Dark Palette (from openclaw/src/tui/theme/theme.ts) ──────

PALETTE = {
    "text": "#E8E3D5",
    "dim": "#7B7F87",
    "accent": "#F6C453",
    "accentSoft": "#F2A65A",
    "border": "#3C414B",
    "userBg": "#2B2F36",
    "userText": "#F3EEE0",
    "systemText": "#9BA3B2",
    "quote": "#8CC8FF",
    "quoteBorder": "#3B4D6B",
    "code": "#F0C987",
    "codeBlock": "#1E232A",
    "codeBorder": "#343A45",
    "link": "#7DD3A5",
    "error": "#F97066",
    "success": "#7DD3A5",
}

# Build theme functions (like OpenClaw's theme object)
theme_fg = _fg(PALETTE["text"])
theme_dim = _fg(PALETTE["dim"])
theme_accent = _fg(PALETTE["accent"])
theme_accent_soft = _fg(PALETTE["accentSoft"])
def theme_header(t: str) -> str:
    return _bold(_fg(PALETTE["accent"])(t))
theme_system = _fg(PALETTE["systemText"])
theme_user_bg = _bg(PALETTE["userBg"])
theme_user_text = _fg(PALETTE["userText"])
theme_error = _fg(PALETTE["error"])
theme_border = _fg(PALETTE["border"])

# Markdown theme (matching OpenClaw's markdownTheme)
MD_THEME = MarkdownTheme(
    heading=lambda t: _bold(_fg(PALETTE["accent"])(t)),
    link=_fg(PALETTE["link"]),
    link_url=lambda t: _dim(t),
    code=_fg(PALETTE["code"]),
    code_block=_fg(PALETTE["code"]),
    code_block_border=_fg(PALETTE["codeBorder"]),
    quote=_fg(PALETTE["quote"]),
    quote_border=_fg(PALETTE["quoteBorder"]),
    hr=theme_border,
    list_bullet=_fg(PALETTE["accentSoft"]),
    bold=_bold,
    italic=_italic,
)

# Editor theme
EDITOR_BORDER = theme_border

# ── Waiting phrases (from OpenClaw tui-waiting.ts) ────────────────────

WAITING_PHRASES = [
    "flibbertigibbeting",
    "kerfuffling",
    "dillydallying",
    "twiddling thumbs",
    "noodling",
    "bamboozling",
    "moseying",
    "hobnobbing",
    "pondering",
    "conjuring",
]

# ── Slash Commands ────────────────────────────────────────────────────

SLASH_COMMANDS = {
    "/quit": "Exit the TUI",
    "/exit": "Exit the TUI",
    "/reset": "Reset chat session",
    "/status": "Show gateway status",
    "/clear": "Clear screen and chat",
}

# ── Ctrl+C handling (matching OpenClaw's resolveCtrlCAction) ──────────


def _resolve_ctrl_c_action(
    has_input: bool, now: float, last_ctrl_c_at: float, exit_window: float = 1.0
) -> tuple[str, float]:
    """Resolve Ctrl+C action: 'clear', 'warn', or 'exit'.

    Matches OpenClaw's resolveCtrlCAction exactly:
    - If editor has input → clear it
    - If double-press within window → exit
    - Otherwise → warn (press again to exit)

    Returns (action, next_last_ctrl_c_at).
    """
    if has_input:
        return ("clear", now)
    if now - last_ctrl_c_at <= exit_window:
        return ("exit", last_ctrl_c_at)
    return ("warn", now)


# ── Shimmer (matching OpenClaw's shimmerText) ─────────────────────────


def _shimmer_text(text: str, tick: int) -> str:
    """Sweep a bold highlight across text (matching OpenClaw tui-waiting.ts)."""
    width = 6
    pos = tick % (len(text) + width)
    start = max(0, pos - width)
    end = min(len(text) - 1, pos)
    chars = []
    for i, ch in enumerate(text):
        if start <= i <= end:
            chars.append(_bold(theme_accent_soft(ch)))
        else:
            chars.append(theme_dim(ch))
    return "".join(chars)


def _pick_waiting_phrase(tick: int, phrases: list[str] | None = None) -> str:
    """Pick phrase rotating every 10 ticks (matching OpenClaw)."""
    ps = phrases or WAITING_PHRASES
    idx = (tick // 10) % len(ps)
    return ps[idx]


def _build_waiting_status(tick: int, elapsed: str, conn_status: str, phrase: str) -> str:
    """Build waiting status message (matching OpenClaw's buildWaitingStatusMessage)."""
    cute = _shimmer_text(f"{phrase}…", tick)
    return f"{cute} • {elapsed} | {conn_status}"


# Known prefixes of internal prompts injected by the goal runner and
# scheduler.  Used as a content-based fallback to filter old transcript
# entries that lack a ``channel`` tag.
_INTERNAL_PROMPT_PREFIXES = (
    "Continue working on your active goals:",
    "[Goal:",      # goal system task instructions
    "Goal completed:",
)


def _is_internal_prompt(content: str) -> bool:
    """Return True if *content* looks like a goal_runner/scheduler prompt."""
    for prefix in _INTERNAL_PROMPT_PREFIXES:
        if content.startswith(prefix):
            return True
    return False


# ── Message Components (matching OpenClaw components exactly) ─────────


class UserMessage(Container):
    """User message: Spacer(1) + Markdown with bgColor/color.

    Matches OpenClaw's UserMessageComponent.
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self.add_child(Spacer(1))
        self.add_child(
            Markdown(
                text,
                1,
                1,
                MD_THEME,
                default_text_style=DefaultTextStyle(
                    color=theme_user_text,
                    bg_color=theme_user_bg,
                ),
            )
        )


class AssistantMessage(Container):
    """Assistant message: Spacer(1) + Markdown with default fg.

    Matches OpenClaw's AssistantMessageComponent:
    assistantText is identity function — keeps terminal default foreground.
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self._body = Markdown(text, 1, 0, MD_THEME)
        self.add_child(Spacer(1))
        self.add_child(self._body)

    def set_text(self, text: str) -> None:
        self._body.set_text(text)


# ── ChatLog (matching OpenClaw's ChatLog exactly) ─────────────────────


class ChatLog(Container):
    """Scrollable chat log with streaming support and component pruning.

    Matches OpenClaw's ChatLog:
    - addUser/addSystem
    - startAssistant/updateAssistant/finalizeAssistant/dropAssistant
    - pruneOverflow at 180 components
    - streaming_runs tracking for run-based message updates
    """

    MAX_COMPONENTS = 180

    def __init__(self) -> None:
        super().__init__()
        self._streaming_runs: dict[str, AssistantMessage] = {}

    def add_user(self, text: str) -> None:
        self._append(UserMessage(text))

    def add_assistant(self, text: str) -> None:
        """Add a completed assistant message (for history replay)."""
        self._append(AssistantMessage(text))

    def add_system(self, text: str) -> None:
        self._append(Spacer(1))
        self._append(Text(theme_system(text), 1, 0))

    def start_assistant(self, text: str, run_id: str = "default") -> AssistantMessage:
        """Start a new assistant message for streaming (matching OpenClaw)."""
        existing = self._streaming_runs.get(run_id)
        if existing is not None:
            existing.set_text(text)
            return existing
        comp = AssistantMessage(text)
        self._streaming_runs[run_id] = comp
        self._append(comp)
        return comp

    def update_assistant(self, text: str, run_id: str = "default") -> None:
        """Update an existing streaming assistant message (matching OpenClaw)."""
        existing = self._streaming_runs.get(run_id)
        if existing is None:
            self.start_assistant(text, run_id)
            return
        existing.set_text(text)

    def finalize_assistant(self, text: str, run_id: str = "default") -> None:
        """Finalize a streaming assistant message (matching OpenClaw)."""
        existing = self._streaming_runs.get(run_id)
        if existing is not None:
            existing.set_text(text)
            del self._streaming_runs[run_id]
            return
        self._append(AssistantMessage(text))

    def drop_assistant(self, run_id: str = "default") -> None:
        """Remove a streaming assistant component (matching OpenClaw)."""
        existing = self._streaming_runs.get(run_id)
        if existing is None:
            return
        self.remove_child(existing)
        del self._streaming_runs[run_id]

    def clear_all(self) -> None:
        self.clear()
        self._streaming_runs.clear()

    def _append(self, comp: Component) -> None:
        self.add_child(comp)
        self._prune()

    def _drop_component_refs(self, comp: Component) -> None:
        """Clean up streaming run references when pruning (matching OpenClaw)."""
        for run_id, msg in list(self._streaming_runs.items()):
            if msg is comp:
                del self._streaming_runs[run_id]

    def _prune(self) -> None:
        while len(self.children) > self.MAX_COMPONENTS:
            oldest = self.children[0]
            self.children.pop(0)
            self._drop_component_refs(oldest)


# ── CustomEditor (matching OpenClaw's CustomEditor) ───────────────────


class _SelectList:
    """Minimal select list matching OpenClaw's SelectList.

    Renders a scrollable list of items with arrow selection,
    description column, and scroll indicator.
    """

    def __init__(self, items: list[tuple[str, str]], max_visible: int = 5) -> None:
        # items: list of (value, description)
        self._items = items
        self._selected = 0
        self._max_visible = max_visible

    @property
    def selected_index(self) -> int:
        return self._selected

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

    def render(self, width: int) -> list[str]:
        if not self._items:
            return [theme_dim("  No matching commands")]

        lines: list[str] = []
        n = len(self._items)

        # Calculate visible window centered on selection
        start = max(
            0,
            min(
                self._selected - self._max_visible // 2,
                n - self._max_visible,
            ),
        )
        end = min(start + self._max_visible, n)

        for i in range(start, end):
            value, desc = self._items[i]
            is_sel = i == self._selected

            if is_sel:
                display = value
                if desc and width > 40:
                    max_val_w = min(30, width - 6)
                    trunc_val = display[:max_val_w]
                    spacing = " " * max(1, 32 - len(trunc_val))
                    remaining = width - 4 - len(trunc_val) - len(spacing)
                    if remaining > 10:
                        trunc_desc = desc[:remaining]
                        line = theme_accent(f"→ {trunc_val}{spacing}{trunc_desc}")
                    else:
                        line = theme_accent(f"→ {display[:width - 6]}")
                else:
                    line = theme_accent(f"→ {display[:width - 6]}")
            else:
                display = value
                if desc and width > 40:
                    max_val_w = min(30, width - 6)
                    trunc_val = display[:max_val_w]
                    spacing = " " * max(1, 32 - len(trunc_val))
                    remaining = width - 4 - len(trunc_val) - len(spacing)
                    if remaining > 10:
                        trunc_desc = desc[:remaining]
                        line = f"  {trunc_val}{spacing}{theme_dim(trunc_desc)}"
                    else:
                        line = f"  {display[:width - 6]}"
                else:
                    line = f"  {display[:width - 6]}"

            lines.append(line)

        # Scroll indicator
        if start > 0 or end < n:
            lines.append(theme_dim(f"  ({self._selected + 1}/{n})"))

        return lines


class CustomEditor(Component):
    """Input editor with borders, slash command select list, and Ctrl+C/D/Escape.

    Matches OpenClaw's CustomEditor which extends pi-tui's Editor:
    - onSubmit, onEscape, onCtrlC, onCtrlD callbacks
    - Border rendering with accent prompt
    - SelectList for slash commands (rendered below bottom border)
    - Up/Down navigate select list when it's showing
    """

    def __init__(self) -> None:
        self._value = ""
        self._cursor = 0
        self._history: list[str] = []
        self._hist_idx = -1
        self._hist_stash = ""
        # Select list state
        self._select_list: _SelectList | None = None
        # Callbacks (matching OpenClaw's CustomEditor fields)
        self.on_submit: Any = None
        self.on_escape: Any = None
        self.on_ctrl_c: Any = None
        self.on_ctrl_d: Any = None

    def get_text(self) -> str:
        return self._value

    def set_text(self, value: str) -> None:
        self._value = value
        self._cursor = len(value)

    def add_to_history(self, text: str) -> None:
        self._history.append(text)
        self._hist_idx = -1

    def invalidate(self) -> None:
        pass

    def _update_select_list(self) -> None:
        """Update the select list based on current input value."""
        if self._value.startswith("/"):
            matching = [
                (cmd, desc)
                for cmd, desc in SLASH_COMMANDS.items()
                if cmd.startswith(self._value) and cmd != self._value
            ]
            if matching:
                if self._select_list is None:
                    self._select_list = _SelectList(matching)
                else:
                    self._select_list.set_items(matching)
            else:
                self._select_list = None
        else:
            self._select_list = None

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        border = theme_border("─" * width)

        # Top border
        lines.append(border)

        # Input line with cursor (matching OpenClaw's Editor render)
        prompt = _bold(theme_accent("> "))
        before = self._value[: self._cursor]
        after = self._value[self._cursor :]
        cursor_ch = after[0] if after else " "
        rest = after[1:] if after else ""
        input_line = prompt + before + f"\x1b[7m{cursor_ch}\x1b[27m" + rest
        lines.append(input_line)

        # Bottom border
        lines.append(border)

        # Select list BELOW the bottom border (matching OpenClaw's Editor
        # which renders autocomplete SelectList after the bottom border)
        if self._select_list is not None:
            lines.extend(self._select_list.render(width))

        return lines

    def handle_input(self, event: KeyEvent) -> bool:
        # Escape — cancel select list if showing, otherwise abort request
        if event.key == "escape":
            if self._select_list is not None:
                self._select_list = None
                return True
            if self.on_escape:
                self.on_escape()
            return True

        # Ctrl+C (matching OpenClaw: double-press to exit)
        if event.ctrl and event.key == "c" and self.on_ctrl_c:
            self.on_ctrl_c()
            return True

        # Ctrl+D (matching OpenClaw: exit only when editor empty)
        if event.ctrl and event.key == "d":
            if len(self._value) == 0 and self.on_ctrl_d:
                self.on_ctrl_d()
            return True

        # When select list is showing, Up/Down navigate it
        if self._select_list is not None:
            if event.key == "up":
                self._select_list.move(-1)
                return True
            if event.key == "down":
                self._select_list.move(1)
                return True
            # Tab or Enter on select list — apply selected item
            if event.key in ("tab", "enter"):
                selected = self._select_list.selected_value
                if selected is not None:
                    self._value = selected
                    self._cursor = len(self._value)
                    self._select_list = None
                    # If Enter, submit immediately
                    if event.key == "enter":
                        value = self._value.strip()
                        self._value = ""
                        self._cursor = 0
                        self._hist_idx = -1
                        if self.on_submit and value:
                            self.on_submit(value)
                    else:
                        # Tab — just complete, update list for potential further typing
                        self._update_select_list()
                return True

        if event.key == "enter":
            raw = self._value
            value = raw.strip()
            self._value = ""
            self._cursor = 0
            self._hist_idx = -1
            self._select_list = None
            if self.on_submit and value:
                self.on_submit(value)
            return True

        if event.key == "backspace" and self._cursor > 0:
            self._value = (
                self._value[: self._cursor - 1] + self._value[self._cursor :]
            )
            self._cursor -= 1
            self._update_select_list()
            return True

        if event.key == "delete" and self._cursor < len(self._value):
            self._value = (
                self._value[: self._cursor] + self._value[self._cursor + 1 :]
            )
            self._update_select_list()
            return True

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
        if event.ctrl and event.key == "k":
            self._value = self._value[: self._cursor]
            self._update_select_list()
            return True
        if event.ctrl and event.key == "u":
            self._value = self._value[self._cursor :]
            self._cursor = 0
            self._update_select_list()
            return True

        # History (up/down) — only when select list is NOT showing
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

        # Printable
        if len(event.key) == 1 and not event.ctrl and not event.alt:
            self._value = (
                self._value[: self._cursor] + event.key + self._value[self._cursor :]
            )
            self._cursor += 1
            self._update_select_list()
            return True

        return False


# ── ChatApp — main application (matching OpenClaw's runTui) ──────────


class ChatApp:
    """Main TUI application matching OpenClaw's architecture exactly.

    Uses TUI (string-based differential renderer) with Container component
    tree: header, chatLog, statusContainer, footer, editor.

    Implements the same patterns as OpenClaw's runTui():
    - Editor submit handler with slash commands and history
    - Ctrl+C double-press logic (clear input → warn → exit)
    - Escape to abort active request
    - Event-based protocol (delta/final/aborted/error)
    - Status management with busy/idle state transitions
    - Waiting shimmer animation
    """

    def __init__(self, socket_path: str, group_id: str = "main") -> None:
        self.socket_path = socket_path
        self.group_id = group_id

        self._state: dict[str, Any] = {
            "model": "unknown",
            "group_id": group_id,
            "session_id": "",
            "connection_status": "connecting",
            "activity_status": "idle",
            "socket_url": f"unix://{socket_path}",
        }

        self._send_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._recv_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        # Active run tracking (matching OpenClaw)
        self._active_run_id: str | None = None
        self._last_ctrl_c_at: float = 0.0
        self._exit_requested = False
        self._has_connected = False

        # Token usage tracking (matching OpenClaw's footer)
        self._usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self._last_turn_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self._context_window = 0
        self._streaming_output_chars = 0  # tracks chars during streaming for live status
        self._turn_input_chars = 0  # chars of input context sent this turn

        # Todo checklist state (ephemeral, within-turn only)
        self._todo_text: Text | None = None
        self._last_todos: list[dict[str, Any]] = []

        # Goal widget state (persistent across turns)
        self._goal_text: Text | None = None
        self._last_goal: dict[str, Any] | None = None

        # Waiting shimmer state (matching OpenClaw)
        self._waiting = False
        self._waiting_tick = 0
        self._waiting_phrase: str | None = None
        self._status_started: float | None = None
        self._last_activity_status = "idle"

        # Build component tree (matching OpenClaw tui.ts exactly)
        self._tui = TUI()

        self._header = Text("", 1, 0)
        self._chat_log = ChatLog()
        self._todo_container = Container()
        self._goal_container = Container()
        self._status_container = Container()
        self._footer = Text("", 1, 0)
        self._editor = CustomEditor()

        # Status components (swapped dynamically like OpenClaw)
        self._status_text: Text | None = Text(theme_dim("connecting..."), 1, 0)
        self._status_loader: Loader | None = None
        self._status_container.add_child(self._status_text)

        root = Container()
        root.add_child(self._header)
        root.add_child(self._chat_log)
        root.add_child(self._todo_container)
        root.add_child(self._goal_container)
        root.add_child(self._status_container)
        root.add_child(self._footer)
        root.add_child(self._editor)

        self._tui.add_child(root)
        self._tui.set_focus(self._editor)

        self._io_thread = threading.Thread(target=self._io_loop, daemon=True)

        # Wire up editor callbacks (matching OpenClaw's editor event wiring)
        self._editor.on_submit = self._handle_submit
        self._editor.on_ctrl_c = self._handle_ctrl_c
        self._editor.on_ctrl_d = self._request_exit
        self._editor.on_escape = self._handle_escape

    def run(self) -> None:
        self._io_thread.start()
        # Initial ping is sent by _io_loop_async on first connect

        # Register tick callback for polling responses + shimmer
        self._tui.on_tick(self._poll)

        # Start TUI (blocking — handles raw mode, rendering)
        self._tui.start()

    # ── Ctrl+C handling (matching OpenClaw's resolveCtrlCAction) ──────

    def _handle_ctrl_c(self) -> None:
        """Handle Ctrl+C with double-press logic (matching OpenClaw exactly)."""
        now = time.time()
        action, next_at = _resolve_ctrl_c_action(
            has_input=len(self._editor.get_text().strip()) > 0,
            now=now,
            last_ctrl_c_at=self._last_ctrl_c_at,
        )
        self._last_ctrl_c_at = next_at

        if action == "clear":
            self._editor.set_text("")
            self._set_activity_status("cleared input; press ctrl+c again to exit")
            self._tui.request_render()
        elif action == "exit":
            self._request_exit()
        else:  # warn
            self._set_activity_status("press ctrl+c again to exit")
            self._tui.request_render()

    def _handle_escape(self) -> None:
        """Handle Escape — abort active request (matching OpenClaw)."""
        if self._active_run_id:
            self._send_queue.put({
                "type": "abort",
                "group_id": self._state.get("group_id", "main"),
                "run_id": self._active_run_id,
            })
            self._chat_log.add_system("run aborted")
            self._chat_log.drop_assistant(self._active_run_id)
            self._active_run_id = None
            self._set_waiting(False)
            self._tui.request_render()

    def _request_exit(self) -> None:
        """Request clean exit (matching OpenClaw's requestExit)."""
        if self._exit_requested:
            return
        self._exit_requested = True
        self._tui.stop()

    # ── Status management (matching OpenClaw's renderStatus) ──────────

    def _ensure_status_text(self) -> None:
        if self._status_text is not None:
            return
        self._status_container.clear()
        if self._status_loader:
            self._status_loader.stop()
            self._status_loader = None
        self._status_text = Text("", 1, 0)
        self._status_container.add_child(self._status_text)

    def _ensure_status_loader(self) -> None:
        if self._status_loader is not None:
            return
        self._status_container.clear()
        self._status_text = None
        self._status_loader = Loader(
            self._tui,
            lambda s: theme_accent(s),
            lambda t: _bold(theme_accent_soft(t)),
            "",
        )
        self._status_container.add_child(self._status_loader)

    _BUSY_STATES = {"sending", "waiting", "streaming", "running"}

    def _render_status(self) -> None:
        """Render status bar (matching OpenClaw's renderStatus exactly)."""
        activity = self._state.get("activity_status", "idle")
        conn = self._state.get("connection_status", "connecting")
        is_busy = activity in self._BUSY_STATES

        if is_busy:
            if self._status_started is None or self._last_activity_status != activity:
                self._status_started = time.time()
            self._ensure_status_loader()
            if activity == "waiting":
                self._update_busy_status()
            else:
                self._update_busy_status()
        else:
            self._status_started = None
            if self._status_loader:
                self._status_loader.stop()
                self._status_loader = None
            self._ensure_status_text()
            text = f"{conn} | {activity}" if activity else conn
            if self._status_text:
                self._status_text.set_text(theme_dim(text))

        self._last_activity_status = activity

    def _set_activity_status(self, status: str) -> None:
        """Set activity status and re-render status bar (matching OpenClaw)."""
        self._state["activity_status"] = status
        self._render_status()

    def _set_connection_status(self, status: str) -> None:
        """Set connection status and re-render status bar."""
        self._state["connection_status"] = status
        self._render_status()

    def _set_waiting(self, waiting: bool) -> None:
        self._waiting = waiting
        if waiting:
            self._waiting_tick = 0
            self._waiting_phrase = random.choice(WAITING_PHRASES)
            self._set_activity_status("waiting")
        else:
            self._waiting_phrase = None
            self._set_activity_status("idle")

    def _format_elapsed(self) -> str:
        if self._status_started is None:
            return "0s"
        total = int(time.time() - self._status_started)
        if total < 60:
            return f"{total}s"
        m, s = divmod(total, 60)
        return f"{m}m {s}s"

    def _format_live_tokens(self) -> str:
        """Format THIS TURN's live token stats for the status bar."""
        # Estimate input for this turn from context sent
        turn_input = max(1, self._turn_input_chars // 4) if self._turn_input_chars else 0
        # Estimate output so far from streaming chars
        turn_output = max(1, self._streaming_output_chars // 4) if self._streaming_output_chars else 0

        parts: list[str] = []
        if turn_input:
            parts.append(f"↑{_format_tokens(turn_input)}")
        if turn_output:
            parts.append(f"↓{_format_tokens(turn_output)}")
        return " ".join(parts)

    def _update_busy_status(self) -> None:
        """Update busy status message (matching OpenClaw's updateBusyStatusMessage)."""
        if not self._status_loader or self._status_started is None:
            return
        activity = self._state.get("activity_status", "")
        conn = self._state.get("connection_status", "connecting")
        elapsed = self._format_elapsed()
        tokens = self._format_live_tokens()
        token_part = f" {tokens}" if tokens else ""

        if activity == "waiting":
            self._waiting_tick += 1
            phrase = self._waiting_phrase or _pick_waiting_phrase(
                self._waiting_tick
            )
            msg = _build_waiting_status(
                self._waiting_tick, elapsed, conn, phrase
            )
            self._status_loader.set_message(f"{msg}{token_part}")
        else:
            self._status_loader.set_message(f"{activity} • {elapsed} | {conn}{token_part}")

    # ── Todo checklist rendering ──────────────────────────────────────

    _TODO_ICONS = {
        "pending": "☐",
        "in_progress": "⧖",
        "completed": "☑",
    }

    def _render_todos(self, todos: list[dict[str, Any]]) -> None:
        """Render a todo checklist into the dedicated todo container."""
        if not todos:
            self._clear_todos()
            return

        self._last_todos = list(todos)
        lines: list[str] = []
        for item in todos:
            status = item.get("status", "pending")
            content = item.get("content", "")
            icon = self._TODO_ICONS.get(status, "☐")

            if status == "completed":
                lines.append(theme_dim(f"  {icon} {content}"))
            elif status == "in_progress":
                lines.append(theme_accent(f"  {icon} {content}"))
            else:
                lines.append(f"  {icon} {content}")

        display = "\n".join(lines)
        if self._todo_text is None:
            self._todo_text = Text(display, 0, 0)
            self._todo_container.add_child(self._todo_text)
        else:
            self._todo_text.set_text(display)

    def _clear_todos(self) -> None:
        """Clear the todo checklist display."""
        if self._todo_text is not None:
            self._todo_container.clear()
            self._todo_text = None
        self._last_todos = []

    def _finalize_todos(self) -> None:
        """Mark all todo items as completed on turn end (keep visible)."""
        if not self._last_todos:
            return
        completed = [
            {**item, "status": "completed"} for item in self._last_todos
        ]
        self._last_todos = completed
        self._render_todos(completed)

    # ── Goal widget rendering ──────────────────────────────────────────

    _GOAL_ICONS = {
        "pending": "☐",
        "in_progress": "⧖",
        "completed": "☑",
        "skipped": "☒",
    }

    def _render_goal(self, goal: dict[str, Any]) -> None:
        """Render a goal with task statuses into the dedicated goal container."""
        self._last_goal = dict(goal)

        title = goal.get("title", "")
        goal_id = goal.get("id", "")
        goal_status = goal.get("status", "active")
        tasks = goal.get("tasks", [])

        lines: list[str] = []
        # Title line
        if goal_status in ("completed", "abandoned"):
            lines.append(theme_dim(f"  {title} [{goal_id}] — {goal_status}"))
        else:
            lines.append(theme_accent(f"  {title} [{goal_id}]"))

        # Task lines
        for task in tasks:
            status = task.get("status", "pending")
            desc = task.get("description", "")
            icon = self._GOAL_ICONS.get(status, "☐")

            if status == "completed":
                lines.append(theme_dim(f"    {icon} {desc}"))
            elif status == "in_progress":
                lines.append(theme_accent(f"    {icon} {desc}"))
            elif status == "skipped":
                lines.append(theme_dim(f"    {icon} {desc}"))
            else:
                lines.append(f"    {icon} {desc}")

        display = "\n".join(lines)
        if self._goal_text is None:
            self._goal_text = Text(display, 0, 0)
            self._goal_container.add_child(self._goal_text)
        else:
            self._goal_text.set_text(display)

    def _clear_goal(self) -> None:
        """Clear the goal widget display."""
        if self._goal_text is not None:
            self._goal_container.clear()
            self._goal_text = None
        self._last_goal = None

    def _finalize_goal(self) -> None:
        """On turn end, clear goal widget if completed/abandoned, otherwise keep."""
        if self._last_goal is None:
            return
        status = self._last_goal.get("status", "active")
        if status in ("completed", "abandoned"):
            self._clear_goal()

    # ── Header/footer (matching OpenClaw's updateHeader/updateFooter) ─

    def _update_header(self) -> None:
        st = self._state
        session_short = st.get("session_id", "?")[:12]
        text = (
            f"claw tui - {st.get('socket_url', '')} "
            f"- agent {st.get('group_id', 'main')} "
            f"- session {session_short}"
        )
        self._header.set_text(theme_header(text))

    def _update_footer(self) -> None:
        """Update footer with SESSION-LEVEL cumulative token stats."""
        st = self._state
        session_short = st.get("session_id", "?")[:12]
        model = st.get("model", "unknown")
        group = st.get("group_id", "main")

        # Build token stats (matching OpenClaw: ↑input ↓output Rcache_read)
        stats: list[str] = []
        u = self._usage
        # Always show input/output — even when zero — so the display is stable
        stats.append(f"↑{_format_tokens(u['input'])}")
        stats.append(f"↓{_format_tokens(u['output'])}")
        if u["cache_read"]:
            stats.append(f"R{_format_tokens(u['cache_read'])}")
        if u["cache_write"]:
            stats.append(f"W{_format_tokens(u['cache_write'])}")

        # Context usage (matching OpenClaw: percent%/context_window)
        if self._context_window > 0:
            context_tokens = _context_usage_tokens(self._last_turn_usage, u)
            pct = (
                min(100.0, context_tokens / self._context_window * 100)
                if context_tokens > 0
                else 0
            )
            ctx_str = f"{pct:.0f}%/{_format_tokens(self._context_window)}"
            stats.append(ctx_str)

        token_str = " ".join(stats) if stats else "tokens 0/0"

        parts = [
            f"agent {group}",
            f"session {session_short}",
            model,
            token_str,
        ]
        self._footer.set_text(theme_dim(" | ".join(parts)))

    # ── Input submission (matching OpenClaw's createEditorSubmitHandler) ─

    def _handle_submit(self, text: str) -> None:
        """Handle editor submit (matching OpenClaw's submit handler).

        Flow: clear editor → add to history → handle slash/message.
        """
        value = text.strip()
        if not value:
            return

        # Add to editor history (matching OpenClaw)
        self._editor.add_to_history(value)

        # Slash commands
        lower = value.lower()
        if lower in ("/quit", "/exit"):
            self._request_exit()
            return
        if lower == "/reset":
            self._send_queue.put({
                "type": "reset",
                "group_id": self._state.get("group_id", "main"),
            })
            self._set_waiting(True)
            return
        if lower == "/clear":
            self._chat_log.clear_all()
            self._tui.request_render()
            return
        if lower == "/status":
            self._send_queue.put({"type": "status"})
            self._set_waiting(True)
            return
        if value.startswith("/"):
            # Unknown slash command
            self._chat_log.add_system(f"Unknown command: {value}")
            self._tui.request_render()
            return

        # Regular message (matching OpenClaw's sendMessage flow)
        self._clear_todos()  # clear any finalized todos from previous turn
        self._clear_goal()   # clear goal widget from previous turn
        self._chat_log.add_user(value)
        self._set_waiting(True)
        self._streaming_output_chars = 0
        # Estimate input for this turn: all prior session context + this message
        self._turn_input_chars = self._usage["input"] * 4 + len(value)
        self._send_queue.put({
            "type": "message",
            "group_id": self._state.get("group_id", "main"),
            "content": value,
        })

    # ── Per-frame polling (matching OpenClaw's event loop) ──

    def _poll(self) -> None:
        changed = False

        while True:
            try:
                msg = self._recv_queue.get_nowait()
                self._handle_response(msg)
                changed = True
            except queue.Empty:
                break

        if self._waiting:
            self._update_busy_status()
            changed = True

        if changed:
            self._tui.request_render()

    # ── Response handling (matching OpenClaw's event handler patterns) ─

    def _handle_response(self, msg: dict) -> None:
        msg_type = msg.get("type")

        if msg_type == "pong":
            # Use session ID from gateway if available (resume existing session),
            # otherwise create a new one
            gateway_session_id = msg.get("session_id")
            gateway_turn_count = msg.get("turn_count", 0)

            if gateway_session_id:
                self._state["session_id"] = gateway_session_id
            elif not self._state.get("session_id"):
                import uuid
                self._state["session_id"] = str(uuid.uuid4())

            # Pick up model info + context window from gateway
            if "model" in msg:
                self._state["model"] = msg["model"]
            if "context_window" in msg:
                self._context_window = msg["context_window"]

            # Load cumulative usage from session transcript
            gw_usage = msg.get("usage")
            if gw_usage:
                self._usage = {
                    "input": gw_usage.get("input", 0),
                    "output": gw_usage.get("output", 0),
                    "cache_read": gw_usage.get("cache_read", 0),
                    "cache_write": gw_usage.get("cache_write", 0),
                }

            self._state["connection_status"] = "connected"
            gid = self._state["group_id"]
            sid = self._state["session_id"][:12]

            # Only show session/history info on first connect (not reconnect)
            if not self._has_connected:
                self._has_connected = True
                self._chat_log.add_system(f"session agent:{gid}:{sid}")

                # Replay chat history from transcript (matching OpenClaw's
                # renderInitialMessages / renderSessionContext)
                history = msg.get("history", [])
                if history:
                    self._chat_log.add_system(
                        f"resumed session: {sid} ({gateway_turn_count} turns)"
                    )
                    for entry in history:
                        role = entry.get("role")
                        content = entry.get("content", "")
                        channel = entry.get("channel", "")
                        if not content:
                            continue
                        # Skip goal_runner/scheduler prompts — they're
                        # internal and shouldn't appear in chat or editor
                        # history (user would see "Continue working on
                        # your active goals..." on up-arrow).
                        if channel in ("goal_runner", "scheduler"):
                            continue
                        # Content-based fallback for old transcripts that
                        # lack the channel tag
                        if role == "user" and _is_internal_prompt(content):
                            continue
                        if role == "user":
                            self._chat_log.add_user(content)
                            # Populate editor history (matching OpenClaw's
                            # populateHistory option)
                            self._editor.add_to_history(content)
                        elif role == "assistant":
                            self._chat_log.add_assistant(content)
                else:
                    self._chat_log.add_system(f"new session: {sid}")

            self._update_header()
            self._update_footer()
            self._set_connection_status("connected")
            self._set_activity_status("idle")
            return

        # Reconnect status update (internal, not from gateway)
        if msg_type == "_reconnect_status":
            attempt = msg.get("attempt", 1)
            delay = msg.get("delay", 1)
            self._set_connection_status("reconnecting")
            self._set_activity_status(
                f"reconnecting (attempt {attempt}, {delay:.0f}s)"
            )
            return

        # Successfully reconnected after disconnection
        if msg_type == "internal_reconnected":
            self._chat_log.add_system("reconnected to gateway")
            self._set_connection_status("connected")
            self._set_activity_status("idle")
            return

        if msg_type == "quit_ack":
            self._tui.stop()
            return

        # Todo checklist update (ephemeral, within-turn progress)
        if msg_type == "todo":
            todos = msg.get("todos", [])
            self._render_todos(todos)
            return

        # Goal widget update (persistent across turns)
        if msg_type == "goal":
            goal = msg.get("goal")
            if goal:
                self._render_goal(goal)
            return

        # Streaming delta (matching OpenClaw's chat event: state=delta)
        if msg_type == "delta":
            run_id = msg.get("run_id", "default")
            content = msg.get("content", "")
            if not self._active_run_id:
                self._active_run_id = run_id
                self._streaming_output_chars = 0
            self._chat_log.update_assistant(content, run_id)
            # content is accumulated text so far — use its length directly
            self._streaming_output_chars = len(content)
            self._update_busy_status()
            self._set_activity_status("streaming")
            return

        # Final response (matching OpenClaw's chat event: state=final)
        if msg_type == "response" or msg_type == "final":
            run_id = msg.get("run_id", "default")
            content = msg.get("content", "")
            self._set_waiting(False)
            self._active_run_id = None
            self._streaming_output_chars = 0
            self._finalize_todos()
            # Clear goal widget only if goal is completed/abandoned;
            # active goals persist across turns
            self._finalize_goal()

            if content:
                # Non-streamed response — finalize with full content
                self._chat_log.finalize_assistant(content, run_id)
            else:
                # Streamed deltas already delivered the content — just
                # close the streaming run without appending a new component.
                existing = self._chat_log._streaming_runs.get(run_id)
                if existing is not None:
                    del self._chat_log._streaming_runs[run_id]
                # If no existing run found, nothing to finalize — the
                # content was already rendered via deltas.

            # Accumulate per-turn usage into session totals
            turn_usage = msg.get("usage")
            if turn_usage:
                self._last_turn_usage = {
                    "input": turn_usage.get("input", 0),
                    "output": turn_usage.get("output", 0),
                    "cache_read": turn_usage.get("cache_read", 0),
                    "cache_write": turn_usage.get("cache_write", 0),
                }
                self._usage["input"] += turn_usage.get("input", 0)
                self._usage["output"] += turn_usage.get("output", 0)
                self._usage["cache_read"] += turn_usage.get("cache_read", 0)
                self._usage["cache_write"] += turn_usage.get("cache_write", 0)

            # Update session info if provided
            if "model" in msg:
                self._state["model"] = msg["model"]
            self._update_footer()
            return

        # Aborted (matching OpenClaw's chat event: state=aborted)
        if msg_type == "aborted":
            run_id = msg.get("run_id", "default")
            self._set_waiting(False)
            self._active_run_id = None
            self._clear_todos()
            self._chat_log.add_system("run aborted")
            self._chat_log.drop_assistant(run_id)
            return

        # Error (matching OpenClaw's chat event: state=error)
        if msg_type in ("error", "internal_error"):
            run_id = msg.get("run_id", "default")
            self._set_waiting(False)
            self._active_run_id = None
            self._clear_todos()
            err = msg.get("message", msg.get("content", "Unknown error"))
            self._chat_log.add_system(theme_error(f"Error: {err}"))
            self._chat_log.drop_assistant(run_id)
            return

        if msg_type == "reset_ack":
            self._set_waiting(False)
            # Use session ID from gateway if provided
            gateway_session_id = msg.get("session_id")
            if gateway_session_id:
                self._state["session_id"] = gateway_session_id
            else:
                import uuid
                self._state["session_id"] = str(uuid.uuid4())
            self._chat_log.clear_all()
            self._usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            self._last_turn_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            sid = self._state["session_id"][:12]
            self._chat_log.add_system(f"new session: {sid}")
            self._update_header()
            self._update_footer()
            return

        if msg_type == "status":
            self._set_waiting(False)
            pid = msg.get("pid", "?")
            groups = ", ".join(msg.get("groups", []))
            self._chat_log.add_system(
                f"Gateway running (PID: {pid}) — groups: {groups}"
            )
            return

    # ── Socket I/O thread with auto-reconnect ──

    # Reconnect settings (matching OpenClaw's auto-retry pattern)
    _RECONNECT_BASE_DELAY = 1.0   # seconds
    _RECONNECT_MAX_DELAY = 30.0   # cap
    _RECONNECT_MAX_RETRIES = 0    # 0 = unlimited

    def _io_loop(self) -> None:
        try:
            asyncio.run(self._io_loop_async())
        except Exception as e:
            # Never let IO thread exceptions spill into the TUI terminal
            self._recv_queue.put({
                "type": "internal_error",
                "message": f"IO thread error: {e}",
            })

    async def _io_loop_async(self) -> None:
        """Connection loop with exponential backoff reconnect.

        Matches OpenClaw's auto-retry pattern:
        - Detects disconnection when read returns empty or raises
        - Exponential backoff: base_delay * 2^(attempt-1), capped
        - Updates TUI status during reconnect attempts
        - Re-sends ping on reconnect to sync session state
        """
        attempt = 0

        while not self._exit_requested:
            sock_path = Path(self.socket_path)

            # ── Connect ──
            if not sock_path.exists():
                if attempt == 0:
                    self._recv_queue.put({
                        "type": "internal_error",
                        "message": f"Socket not found: {sock_path}. Is gateway running?",
                    })
                else:
                    self._set_reconnect_status(attempt)
                attempt += 1
                delay = self._reconnect_delay(attempt)
                await asyncio.sleep(delay)
                continue

            try:
                reader, writer = await asyncio.open_unix_connection(
                    str(sock_path)
                )
            except Exception as e:
                if attempt == 0:
                    self._recv_queue.put({
                        "type": "internal_error",
                        "message": f"Connection error: {e}",
                    })
                else:
                    self._set_reconnect_status(attempt)
                attempt += 1
                delay = self._reconnect_delay(attempt)
                await asyncio.sleep(delay)
                continue

            # ── Connected — reset attempt counter ──
            if attempt > 0:
                # We just reconnected after a disconnection
                self._recv_queue.put({
                    "type": "internal_reconnected",
                })
            attempt = 0

            # Re-send ping to sync session state
            self._send_queue.put({"type": "ping", "group_id": self.group_id})

            # ── Run read/write until disconnect ──
            disconnected = await self._run_connection(reader, writer)

            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

            if not disconnected or self._exit_requested:
                break

            # Connection lost — show status and retry
            attempt = 1
            self._set_reconnect_status(attempt)
            delay = self._reconnect_delay(attempt)
            await asyncio.sleep(delay)

    def _reconnect_delay(self, attempt: int) -> float:
        """Exponential backoff delay (matching OpenClaw's baseDelayMs * 2^(attempt-1))."""
        delay = self._RECONNECT_BASE_DELAY * (2 ** (attempt - 1))
        return min(delay, self._RECONNECT_MAX_DELAY)

    def _set_reconnect_status(self, attempt: int) -> None:
        """Update UI status during reconnect attempts."""
        delay = self._reconnect_delay(attempt)
        self._state["connection_status"] = "reconnecting"
        self._state["activity_status"] = f"reconnecting (attempt {attempt}, {delay:.0f}s)"
        self._recv_queue.put({
            "type": "_reconnect_status",
            "attempt": attempt,
            "delay": delay,
        })

    async def _run_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> bool:
        """Run read/write loops on an established connection.

        Returns True if disconnected (should retry), False if clean exit.
        """
        disconnected = False

        async def _read() -> None:
            nonlocal disconnected
            while not self._exit_requested:
                try:
                    line = await reader.readline()
                    if not line:
                        disconnected = True
                        self._recv_queue.put({
                            "type": "internal_error",
                            "message": "Gateway closed connection. Reconnecting...",
                        })
                        break
                    self._recv_queue.put(
                        json.loads(line.decode("utf-8").strip())
                    )
                except json.JSONDecodeError:
                    pass
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    disconnected = True
                    self._recv_queue.put({
                        "type": "internal_error",
                        "message": f"Read error: {e}. Reconnecting...",
                    })
                    break

        async def _write() -> None:
            while not self._exit_requested:
                try:
                    req = self._send_queue.get_nowait()
                    if req.get("type") == "quit":
                        self._recv_queue.put({"type": "quit_ack"})
                        break
                    if "group_id" not in req:
                        req["group_id"] = self.group_id
                    writer.write(
                        (json.dumps(req) + "\n").encode("utf-8")
                    )
                    await writer.drain()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                except Exception as e:
                    self._recv_queue.put({
                        "type": "internal_error",
                        "message": f"Write error: {e}. Reconnecting...",
                    })
                    break

        read_task = asyncio.create_task(_read())
        write_task = asyncio.create_task(_write())
        await asyncio.wait(
            [read_task, write_task], return_when=asyncio.FIRST_COMPLETED
        )

        # Cancel the remaining task
        for task in [read_task, write_task]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        return disconnected


def run_tui_app(
    socket_path: str, group_id: str = "main", *, model: str = ""
) -> None:
    app = ChatApp(socket_path, group_id)
    if model:
        app._state["model"] = model
    app.run()
