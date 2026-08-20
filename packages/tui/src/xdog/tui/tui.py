"""TUI engine — string-based component rendering with differential updates.

Direct port of the original TypeScript pi-tui architecture:

- ``Component.render(width) → list[str]`` — returns ANSI-styled lines
- ``Container`` — concatenates children's ``render()`` output
- ``TUI extends Container`` — diffs string arrays for differential rendering

Key design: renders in the **main terminal buffer** (no alternate screen)
so terminal scrollback is preserved. Uses relative cursor movements
and natural ``\\r\\n`` scrolling, matching the TypeScript implementation exactly.

Usage::

    tui = TUI()
    tui.add_child(Text("Hello"))
    tui.add_child(Spacer(1))
    tui.add_child(editor)
    tui.set_focus(editor)
    tui.start()  # blocking
"""

from __future__ import annotations

import os
import signal
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Literal

from xdog.tui.keys import KeyEvent, is_key_release, parse_key_events
from xdog.tui.stdin_buffer import KeyBytes, Paste, StdinBuffer
from xdog.tui.terminal_protocol import TerminalProtocol

# ---------------------------------------------------------------------------
# Focusable protocol and cursor marker
# ---------------------------------------------------------------------------

CURSOR_MARKER = "\x1b_pi:c\x07"
"""APC escape sequence used by focused components to mark the cursor position.

TUI finds and strips this marker, then positions the hardware cursor there.
"""


class Focusable:
    """Mix-in for components that can receive focus and display a hardware cursor."""

    focused: bool = False


def is_focusable(component: Component | None) -> bool:
    """Return ``True`` if *component* implements the :class:`Focusable` protocol."""
    return component is not None and hasattr(component, "focused")


# ---------------------------------------------------------------------------
# Overlay types
# ---------------------------------------------------------------------------

OverlayAnchor = Literal[
    "center",
    "top-left", "top-right",
    "bottom-left", "bottom-right",
    "top-center", "bottom-center",
    "left-center", "right-center",
]

SizeValue = int | str  # int for absolute, "50%" for percentage


@dataclass(frozen=True)
class OverlayMargin:
    """Margin from terminal edges for overlays."""

    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0


@dataclass(frozen=True)
class OverlayOptions:
    """Configuration for overlay positioning and sizing."""

    width: SizeValue | None = None
    min_width: int | None = None
    max_height: SizeValue | None = None
    anchor: OverlayAnchor = "center"
    offset_x: int = 0
    offset_y: int = 0
    row: SizeValue | None = None
    col: SizeValue | None = None
    margin: OverlayMargin | int | None = None
    visible: Callable[[int, int], bool] | None = None
    non_capturing: bool = False


def _parse_size_value(value: SizeValue | None, reference: int) -> int | None:
    """Resolve a :class:`SizeValue` to an absolute int."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.endswith("%"):
        try:
            pct = float(value[:-1])
            return int(reference * pct / 100)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Component interface (matches TypeScript Component)
# ---------------------------------------------------------------------------

class Component(ABC):
    """Base class for all TUI components.

    Subclasses implement ``render(width) → list[str]``.
    """

    wants_key_release: bool = False
    """Set to ``True`` if this component needs key release events (Kitty protocol)."""

    @abstractmethod
    def render(self, width: int) -> list[str]:
        """Render to ANSI-styled terminal lines."""
        ...

    def handle_input(self, event: KeyEvent) -> bool:
        """Handle keyboard input. Return True if consumed."""
        return False

    def handle_paste(self, text: str) -> bool:
        """Handle an atomic bracketed-paste payload."""
        return False

    def invalidate(self) -> None:
        """Clear cached rendering state."""
        pass


# ---------------------------------------------------------------------------
# Input listener type
# ---------------------------------------------------------------------------

InputListenerResult = dict[str, object] | None  # {"consume": bool, "data": str} or None
InputListener = Callable[[KeyEvent], InputListenerResult]


# ---------------------------------------------------------------------------
# Container (matches TypeScript Container)
# ---------------------------------------------------------------------------

class Container(Component):
    """Stacks children vertically by concatenating their ``render()`` output."""

    def __init__(self) -> None:
        self.children: list[Component] = []

    def add_child(self, component: Component) -> None:
        self.children.append(component)

    def remove_child(self, component: Component) -> None:
        if component in self.children:
            self.children.remove(component)

    def clear(self) -> None:
        self.children.clear()

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        for child in self.children:
            lines.extend(child.render(width))
        return lines


# ---------------------------------------------------------------------------
# Overlay handle
# ---------------------------------------------------------------------------

class OverlayHandle:
    """Handle for controlling an overlay shown via :meth:`TUI.show_overlay`."""

    def __init__(self, tui: TUI, entry: _OverlayEntry) -> None:
        self._tui = tui
        self._entry = entry

    def hide(self) -> None:
        """Permanently remove the overlay and restore its prior focus."""
        if self._entry in self._tui._overlay_stack:
            was_focused = self._tui._focused is self._entry.component
            self._tui._overlay_stack.remove(self._entry)
            if was_focused:
                replacement = self._tui._get_topmost_visible_overlay()
                self._tui.set_focus(
                    replacement.component
                    if replacement is not None
                    else self._entry.previous_focus
                )
            if not self._tui._overlay_stack:
                sys.stdout.write("\x1b[?25l")  # Hide cursor
            self._tui.request_render()

    def set_hidden(self, hidden: bool) -> None:
        """Temporarily hide or show the overlay."""
        self._entry.hidden = hidden

    def is_hidden(self) -> bool:
        """Check if overlay is temporarily hidden."""
        return self._entry.hidden

    def focus(self) -> None:
        """Focus this overlay and bring it to the front."""
        self._tui._focused = self._entry.component

    def unfocus(self) -> None:
        """Release focus to the previous target."""
        self._tui._focused = None

    def is_focused(self) -> bool:
        """Check if this overlay has focus."""
        return self._tui._focused is self._entry.component


class _OverlayEntry:
    """Internal overlay state."""

    __slots__ = ("component", "options", "hidden", "previous_focus")

    def __init__(
        self,
        component: Component,
        options: OverlayOptions | None,
        previous_focus: Component | None = None,
    ) -> None:
        self.component = component
        self.options = options or OverlayOptions()
        self.hidden = False
        self.previous_focus = previous_focus


# ---------------------------------------------------------------------------
# TUI (matches TypeScript TUI extends Container)
# ---------------------------------------------------------------------------

class TUI(Container):
    """Main TUI with differential string-line rendering and overlay support.

    Faithful port of the TypeScript ``TUI`` class. Renders in the **main
    terminal buffer** (no alternate screen) so terminal scrollback is
    preserved.
    """

    def __init__(self, *, fullscreen: bool = False) -> None:
        super().__init__()
        self.fullscreen = fullscreen
        self._previous_lines: list[str] = []
        self._previous_width: int = 0
        self._previous_height: int = 0
        self._focused: Component | None = None
        self._render_requested: bool = False
        self._cursor_row: int = 0
        self._hardware_cursor_row: int = 0
        self._max_lines_rendered: int = 0
        self._previous_viewport_top: int = 0
        self._stopped: bool = False
        self._running: bool = False
        self._tick_callbacks: list[Callable[[], None]] = []
        self._frame_rate: float = 30.0
        self._overlay_stack: list[_OverlayEntry] = []
        self._input_listeners: list[InputListener] = []
        self._full_redraw_counter: int = 0
        self._suspend_requested = False

    def _terminal_enter_sequence(self) -> str:
        prefix = "\x1b[?1049h" if self.fullscreen else ""
        return prefix + "\x1b[?7l\x1b[?25l\x1b[2J\x1b[H"

    def _terminal_leave_sequence(self) -> str:
        suffix = "\x1b[?1049l" if self.fullscreen else ""
        return "\x1b[?7h\x1b[?25h" + suffix

    # -- overlay management --------------------------------------------------

    def show_overlay(
        self,
        component: Component,
        options: OverlayOptions | None = None,
    ) -> OverlayHandle:
        """Show an overlay component on top of the main content."""
        entry = _OverlayEntry(component, options, self._focused)
        self._overlay_stack.append(entry)
        if not (options and options.non_capturing):
            self._focused = component
        self._render_requested = True
        return OverlayHandle(self, entry)

    def hide_overlay(self) -> None:
        """Pop the topmost overlay."""
        if self._overlay_stack:
            self._overlay_stack.pop()
            if not self._overlay_stack:
                sys.stdout.write("\x1b[?25l")
                self._focused = None
        self._render_requested = True

    def has_overlay(self) -> bool:
        """Return ``True`` if any overlay is currently visible."""
        return any(self._is_overlay_visible(e) for e in self._overlay_stack)

    def _is_overlay_visible(self, entry: _OverlayEntry) -> bool:
        if entry.hidden:
            return False
        if entry.options.visible is not None:
            w = _terminal_width()
            h = _terminal_height()
            return entry.options.visible(w, h)
        return True

    def _get_topmost_visible_overlay(self) -> _OverlayEntry | None:
        for i in range(len(self._overlay_stack) - 1, -1, -1):
            entry = self._overlay_stack[i]
            if entry.options.non_capturing:
                continue
            if self._is_overlay_visible(entry):
                return entry
        return None

    # -- input listeners -----------------------------------------------------

    def add_input_listener(self, listener: InputListener) -> None:
        """Register an input listener called before normal dispatch."""
        self._input_listeners.append(listener)

    def remove_input_listener(self, listener: InputListener) -> None:
        """Remove a previously registered input listener."""
        if listener in self._input_listeners:
            self._input_listeners.remove(listener)

    # -- focus ---------------------------------------------------------------

    def set_focus(self, component: Component | None) -> None:
        # Update Focusable state
        if is_focusable(self._focused):
            self._focused.focused = False  # type: ignore[union-attr]
        self._focused = component
        if is_focusable(component):
            component.focused = True  # type: ignore[union-attr]

    # -- tick callbacks ------------------------------------------------------

    def on_tick(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked each frame."""
        self._tick_callbacks.append(callback)

    # -- render request ------------------------------------------------------

    def request_render(self, force: bool = False) -> None:
        """Mark the screen as dirty so it will be redrawn."""
        if force:
            self._previous_lines = []
            self._previous_width = -1
            self._previous_height = -1
            self._cursor_row = 0
            self._hardware_cursor_row = 0
            self._max_lines_rendered = 0
            self._previous_viewport_top = 0
            self._full_redraw_counter += 1
        self._render_requested = True

    # -- properties ----------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    # -- main loop -----------------------------------------------------------

    def suspend(self) -> bool:
        """Request a controlled POSIX job-control suspend."""
        if os.name == "nt" or not hasattr(signal, "SIGTSTP"):
            return False
        self._suspend_requested = True
        self._running = False
        return True

    def start(self) -> None:
        """Start the TUI main loop (blocking)."""
        self._running = True
        self._stopped = False
        stdin_buf = StdinBuffer()
        protocol = TerminalProtocol()
        stdin_buf.enter_raw()

        sys.stdout.write(protocol.startup())
        sys.stdout.write(self._terminal_enter_sequence())
        sys.stdout.flush()

        frame_interval = 1.0 / self._frame_rate

        try:
            self.request_render()

            while self._running:
                frame_start = time.monotonic()

                # Input
                data = stdin_buf.read(timeout=frame_interval)
                if data:
                    data = protocol.filter_input(data)
                    protocol_output = protocol.pending_output()
                    if protocol_output:
                        sys.stdout.write(protocol_output)
                        sys.stdout.flush()
                    consumed = False
                    for frame in stdin_buf.feed(data):
                        if isinstance(frame, Paste):
                            consumed = self._dispatch_paste(frame.text) or consumed
                            continue
                        assert isinstance(frame, KeyBytes)
                        for event in parse_key_events(frame.data):
                            # Filter key releases unless component wants them.
                            if is_key_release(event):
                                if self._focused and getattr(self._focused, "wants_key_release", False):
                                    consumed = self._dispatch_input(event) or consumed
                                continue
                            consumed = self._dispatch_input(event) or consumed
                    if consumed:
                        self._render_requested = True
                else:
                    for frame in stdin_buf.flush_expired():
                        if isinstance(frame, KeyBytes):
                            for event in parse_key_events(frame.data):
                                if self._dispatch_input(event):
                                    self._render_requested = True

                # Detect terminal resize
                cur_w = _terminal_width()
                cur_h = _terminal_height()
                if (
                    self._previous_width != 0
                    and (cur_w != self._previous_width or cur_h != self._previous_height)
                ):
                    self._render_requested = True

                # Tick callbacks
                for cb in self._tick_callbacks:
                    cb()

                # Render
                if self._render_requested:
                    self._do_render()
                    self._render_requested = False

                # Frame pacing
                elapsed = time.monotonic() - frame_start
                remaining = frame_interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            suspended = self._suspend_requested
            self._suspend_requested = False
            sys.stdout.write(protocol.cleanup())
            sys.stdout.write(self._terminal_leave_sequence())
            sys.stdout.flush()

            if self._previous_lines:
                target_row = len(self._previous_lines)
                diff = target_row - self._hardware_cursor_row
                if diff > 0:
                    sys.stdout.write(f"\x1b[{diff}B")
                elif diff < 0:
                    sys.stdout.write(f"\x1b[{-diff}A")
                sys.stdout.write("\r\n")
            sys.stdout.flush()
            stdin_buf.restore()
            self._running = False

        if suspended and os.name != "nt" and hasattr(signal, "SIGTSTP"):
            os.killpg(os.getpgrp(), signal.SIGTSTP)
            self._previous_width = 0
            self._previous_height = 0
            self.request_render(force=True)
            self.start()

    def stop(self) -> None:
        """Signal the main loop to exit."""
        self._stopped = True
        self._running = False

    # -- input dispatch ------------------------------------------------------

    def _dispatch_paste(self, text: str) -> bool:
        overlay = self._get_topmost_visible_overlay()
        if overlay is not None and overlay.component.handle_paste(text):
            return True
        if self._focused is not None and self._focused is not (
            overlay.component if overlay is not None else None
        ):
            if self._focused.handle_paste(text):
                return True
        return self.handle_paste(text)

    def _dispatch_input(self, event: KeyEvent) -> bool:
        """Dispatch input and report whether a component consumed it."""
        # Escape belongs to the most specific active surface first. This lets a
        # permission panel deny one call instead of the global listener
        # canceling the entire turn.
        if event.matches("escape"):
            overlay = self._get_topmost_visible_overlay()
            offered = overlay.component if overlay is not None else None
            if offered is not None and offered.handle_input(event):
                return True
            if (
                self._focused is not None
                and self._focused is not offered
                and self._focused.handle_input(event)
            ):
                return True

        # Global listeners get first pass for non-contextual shortcuts.
        for listener in self._input_listeners:
            result = listener(event)
            if result and result.get("consume"):
                return True

        # Overlay gets focus if topmost.
        overlay = self._get_topmost_visible_overlay()
        if overlay is not None:
            if event.matches("escape"):
                return False
            return overlay.component.handle_input(event)

        if self._focused is not None and not event.matches("escape"):
            if self._focused.handle_input(event):
                return True
        return self.handle_input(event)

    # -- overlay compositing -------------------------------------------------

    def _composite_overlays(self, base_lines: list[str], width: int, height: int) -> list[str]:
        """Composite overlay content on top of base content."""
        if not self._overlay_stack:
            return base_lines

        # Overlay coordinates are viewport-relative, while base_lines contains
        # the entire main-buffer history. Positioning at row 0 of base_lines
        # puts a dialog near the beginning of a long conversation, outside the
        # currently visible terminal viewport. Translate screen rows to the
        # tail viewport before compositing.
        result = list(base_lines)
        viewport_top = max(0, len(result) - height)
        while len(result) < viewport_top + height:
            result.append("")

        for entry in self._overlay_stack:
            if not self._is_overlay_visible(entry):
                continue

            opts = entry.options
            margin = opts.margin
            if isinstance(margin, int):
                margin = OverlayMargin(top=margin, right=margin, bottom=margin, left=margin)
            elif margin is None:
                margin = OverlayMargin()

            # Determine overlay width
            ov_width = _parse_size_value(opts.width, width)
            if ov_width is None:
                ov_width = width - margin.left - margin.right
            if opts.min_width is not None:
                ov_width = max(ov_width, opts.min_width)
            ov_width = min(ov_width, width - margin.left - margin.right)

            # Render overlay
            ov_lines = entry.component.render(ov_width)

            # Apply max_height
            max_h = _parse_size_value(opts.max_height, height)
            if max_h is not None:
                ov_lines = ov_lines[:max_h]

            ov_height = len(ov_lines)

            # Calculate position based on anchor
            row, col = self._calculate_overlay_position(
                opts, ov_width, ov_height, width, height, margin
            )

            # Composite overlay onto result
            for i, ov_line in enumerate(ov_lines):
                target_row = viewport_top + row + i
                if 0 <= target_row < len(result):
                    base = result[target_row]
                    # Pad base line to width if needed
                    base_padded = base + " " * max(0, width - len(base))
                    # Replace columns [col, col+ov_width) with overlay content
                    prefix = base_padded[:col] if col < len(base_padded) else base_padded
                    suffix_start = col + ov_width
                    suffix = base_padded[suffix_start:] if suffix_start < len(base_padded) else ""
                    result[target_row] = prefix + ov_line + suffix

        return result[:max(len(base_lines), viewport_top + height)]

    def _calculate_overlay_position(
        self,
        opts: OverlayOptions,
        ov_width: int,
        ov_height: int,
        term_width: int,
        term_height: int,
        margin: OverlayMargin,
    ) -> tuple[int, int]:
        """Calculate (row, col) for an overlay based on its anchor."""
        # Explicit row/col takes priority
        if opts.row is not None or opts.col is not None:
            row = _parse_size_value(opts.row, term_height) or 0
            col = _parse_size_value(opts.col, term_width) or 0
            return row + opts.offset_y, col + opts.offset_x

        anchor = opts.anchor
        avail_w = term_width - margin.left - margin.right
        avail_h = term_height - margin.top - margin.bottom

        # Vertical position
        if anchor.startswith("top"):
            row = margin.top
        elif anchor.startswith("bottom"):
            row = margin.top + avail_h - ov_height
        else:  # center, left-center, right-center
            row = margin.top + (avail_h - ov_height) // 2

        # Horizontal position
        if anchor.endswith("left"):
            col = margin.left
        elif anchor.endswith("right"):
            col = margin.left + avail_w - ov_width
        else:  # center, top-center, bottom-center
            col = margin.left + (avail_w - ov_width) // 2

        return max(0, row + opts.offset_y), max(0, col + opts.offset_x)

    # -- differential renderer (matches TypeScript TUI.doRender) -------------

    def _do_render(self) -> None:
        """Render with differential updates in the main terminal buffer.

        Faithful port of the TypeScript ``doRender()`` method:
        1. Render all components to string lines
        2. Compare with previous frame to find changes
        3. Use relative cursor movements to update only changed lines
        4. Scroll viewport naturally via ``\\r\\n`` when content grows
        """
        if self._stopped:
            return

        width = _terminal_width()
        height = _terminal_height()
        viewport_top = max(0, self._max_lines_rendered - height)
        prev_viewport_top = self._previous_viewport_top
        hardware_cursor_row = self._hardware_cursor_row

        def compute_line_diff(target_row: int) -> int:
            """Compute relative cursor movement from current to target row."""
            current_screen_row = hardware_cursor_row - prev_viewport_top
            target_screen_row = target_row - viewport_top
            return target_screen_row - current_screen_row

        # Detect resize before rendering so width-dependent components are
        # invalidated once and rendered once at the new dimensions.
        width_changed = self._previous_width != 0 and self._previous_width != width
        height_changed = self._previous_height != 0 and self._previous_height != height
        if width_changed or height_changed:
            self.invalidate()
            for entry in self._overlay_stack:
                entry.component.invalidate()

        # Render all components to get new lines.
        new_lines = self.render(width)

        # Composite overlays on top
        if self._overlay_stack:
            new_lines = self._composite_overlays(new_lines, width, height)

        cursor_target: tuple[int, int] | None = None
        clean_lines: list[str] = []
        visible_top = max(0, len(new_lines) - height)
        for row, line in enumerate(new_lines):
            marker_index = line.find(CURSOR_MARKER)
            if marker_index >= 0:
                if row >= visible_top:
                    from xdog.tui.utils import string_width
                    cursor_target = (row, string_width(line[:marker_index]))
                line = line.replace(CURSOR_MARKER, "")
            clean_lines.append(line)
        new_lines = clean_lines

        def position_cursor(buf: str, current_row: int) -> tuple[str, int]:
            if cursor_target is None:
                return buf, current_row
            target_row, target_col = cursor_target
            current_screen_row = current_row - viewport_top
            target_screen_row = target_row - viewport_top
            delta = target_screen_row - current_screen_row
            if delta < 0:
                buf += f"\x1b[{-delta}A"
            elif delta > 0:
                buf += f"\x1b[{delta}B"
            return buf + f"\x1b[{target_col + 1}G", target_row

        # -- fullRender helper (matches TypeScript) --------------------------
        def full_render(clear: bool) -> None:
            nonlocal hardware_cursor_row, viewport_top
            buf = "\x1b[?2026h"  # Begin synchronized output
            if clear:
                # Repaint the active viewport in place. ED2 (CSI 2 J) is not
                # safe for a main-buffer TUI: some terminals preserve its old
                # screen as scrollback, producing snapshots that contain the
                # editor and duplicate the current chat. Moving to the top and
                # erasing each row neither scrolls nor touches history.
                current_screen_row = max(
                    0,
                    min(height - 1, hardware_cursor_row - prev_viewport_top),
                )
                if current_screen_row > 0:
                    buf += f"\x1b[{current_screen_row}A"
                buf += "\r"
                visible_lines = new_lines[max(0, len(new_lines) - height):]
                for screen_row in range(height):
                    buf += "\x1b[2K"
                    if screen_row < len(visible_lines):
                        buf += visible_lines[screen_row]
                    if screen_row < height - 1:
                        buf += "\r\n"
                # The repaint ends on the last terminal row. Return the cursor
                # to the final logical line when content is shorter than the
                # viewport, leaving cleared rows below it.
                target_screen_row = max(0, len(visible_lines) - 1)
                move_up = height - 1 - target_screen_row
                if move_up > 0:
                    buf += f"\x1b[{move_up}A"
                buf += "\r"
            else:
                # Initial render writes history once so genuine terminal
                # scrollback is created.
                for i, line in enumerate(new_lines):
                    if i > 0:
                        buf += "\r\n"
                    buf += line
            buf += "\x1b[?2026l"  # End synchronized output
            render_row = max(0, len(new_lines) - 1)
            buf, positioned_row = position_cursor(buf, render_row)
            sys.stdout.write(buf)
            sys.stdout.flush()
            self._cursor_row = render_row
            self._hardware_cursor_row = positioned_row
            # Reset max lines when clearing, otherwise track growth
            if clear:
                self._max_lines_rendered = len(new_lines)
            else:
                self._max_lines_rendered = max(
                    self._max_lines_rendered, len(new_lines)
                )
            self._previous_viewport_top = max(
                0, self._max_lines_rendered - height
            )
            self._previous_lines = new_lines
            self._previous_width = width
            self._previous_height = height

        # -- First render (no previous state) --------------------------------
        if not self._previous_lines and not width_changed and not height_changed:
            full_render(False)
            return

        # -- Width or height changed -----------------------------------------
        if width_changed or height_changed:
            full_render(True)
            return

        # -- Content shrunk below working area — clear and re-render ---------
        if len(new_lines) < self._max_lines_rendered:
            full_render(True)
            return

        # -- Find first and last changed lines -------------------------------
        first_changed = -1
        last_changed = -1
        max_len = max(len(new_lines), len(self._previous_lines))
        for i in range(max_len):
            old_line = self._previous_lines[i] if i < len(self._previous_lines) else ""
            new_line = new_lines[i] if i < len(new_lines) else ""
            if old_line != new_line:
                if first_changed == -1:
                    first_changed = i
                last_changed = i

        appended_lines = len(new_lines) > len(self._previous_lines)
        if appended_lines:
            if first_changed == -1:
                first_changed = len(self._previous_lines)
            last_changed = len(new_lines) - 1

        append_start = (
            appended_lines
            and first_changed == len(self._previous_lines)
            and first_changed > 0
        )

        # -- No changes ------------------------------------------------------
        if first_changed == -1:
            self._previous_viewport_top = max(
                0, self._max_lines_rendered - height
            )
            self._previous_height = height
            return

        # -- All changes in deleted lines ------------------------------------
        if first_changed >= len(new_lines):
            if len(self._previous_lines) > len(new_lines):
                buf = "\x1b[?2026h"
                target_row = max(0, len(new_lines) - 1)
                line_diff = compute_line_diff(target_row)
                if line_diff > 0:
                    buf += f"\x1b[{line_diff}B"
                elif line_diff < 0:
                    buf += f"\x1b[{-line_diff}A"
                buf += "\r"
                extra_lines = len(self._previous_lines) - len(new_lines)
                if extra_lines > height:
                    full_render(True)
                    return
                if extra_lines > 0:
                    buf += "\x1b[1B"
                for i in range(extra_lines):
                    buf += "\r\x1b[2K"
                    if i < extra_lines - 1:
                        buf += "\x1b[1B"
                if extra_lines > 0:
                    buf += f"\x1b[{extra_lines}A"
                buf += "\x1b[?2026l"
                buf, positioned_row = position_cursor(buf, target_row)
                sys.stdout.write(buf)
                sys.stdout.flush()
                self._cursor_row = target_row
                self._hardware_cursor_row = positioned_row
            self._previous_lines = new_lines
            self._previous_width = width
            self._previous_height = height
            self._previous_viewport_top = max(
                0, self._max_lines_rendered - height
            )
            return

        # -- First change above previous viewport — full re-render -----------
        previous_content_viewport_top = max(
            0, len(self._previous_lines) - height
        )
        if first_changed < previous_content_viewport_top:
            full_render(True)
            return

        # -- Differential render (only changed lines) ------------------------
        buf = "\x1b[?2026h"  # Begin synchronized output
        prev_viewport_bottom = prev_viewport_top + height - 1
        move_target_row = (first_changed - 1) if append_start else first_changed

        # Scroll down if target is below current viewport
        if move_target_row > prev_viewport_bottom:
            current_screen_row = max(
                0, min(height - 1, hardware_cursor_row - prev_viewport_top)
            )
            move_to_bottom = height - 1 - current_screen_row
            if move_to_bottom > 0:
                buf += f"\x1b[{move_to_bottom}B"
            scroll = move_target_row - prev_viewport_bottom
            buf += "\r\n" * scroll
            prev_viewport_top += scroll
            viewport_top += scroll
            hardware_cursor_row = move_target_row

        # Move cursor to first changed line
        line_diff = compute_line_diff(move_target_row)
        if line_diff > 0:
            buf += f"\x1b[{line_diff}B"  # Move down
        elif line_diff < 0:
            buf += f"\x1b[{-line_diff}A"  # Move up

        buf += "\r\n" if append_start else "\r"  # Move to column 0

        # Render only changed lines (firstChanged to lastChanged)
        render_end = min(last_changed, len(new_lines) - 1)
        for i in range(first_changed, render_end + 1):
            if i > first_changed:
                buf += "\r\n"
            buf += "\x1b[2K"  # Clear current line
            buf += new_lines[i]

        buf += "\x1b[?2026l"  # End synchronized output

        # Update state
        final_cursor_row = render_end
        buf, positioned_row = position_cursor(buf, final_cursor_row)
        sys.stdout.write(buf)
        sys.stdout.flush()
        self._cursor_row = max(0, len(new_lines) - 1)
        self._hardware_cursor_row = positioned_row
        self._max_lines_rendered = max(self._max_lines_rendered, len(new_lines))
        self._previous_viewport_top = max(
            0, self._max_lines_rendered - height
        )
        self._previous_lines = new_lines
        self._previous_width = width
        self._previous_height = height


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _terminal_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def _terminal_height() -> int:
    try:
        return os.get_terminal_size().lines
    except OSError:
        return 24
