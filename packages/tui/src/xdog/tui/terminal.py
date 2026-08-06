"""Terminal abstraction with differential rendering.

Manages a 2D grid of :class:`Cell` objects and only writes to stdout the cells
that have changed between frames, dramatically reducing I/O and flicker.
"""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TextIO

from xdog.tui.utils import char_width

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Color:
    """An RGB colour value.

    Also supports the 16 standard terminal colours via class methods.
    """

    r: int
    g: int
    b: int

    # Named standard colours
    @classmethod
    def black(cls) -> Color:
        return cls(0, 0, 0)

    @classmethod
    def red(cls) -> Color:
        return cls(205, 0, 0)

    @classmethod
    def green(cls) -> Color:
        return cls(0, 205, 0)

    @classmethod
    def yellow(cls) -> Color:
        return cls(205, 205, 0)

    @classmethod
    def blue(cls) -> Color:
        return cls(0, 0, 238)

    @classmethod
    def magenta(cls) -> Color:
        return cls(205, 0, 205)

    @classmethod
    def cyan(cls) -> Color:
        return cls(0, 205, 205)

    @classmethod
    def white(cls) -> Color:
        return cls(229, 229, 229)

    @classmethod
    def bright_black(cls) -> Color:
        return cls(127, 127, 127)

    @classmethod
    def bright_red(cls) -> Color:
        return cls(255, 0, 0)

    @classmethod
    def bright_green(cls) -> Color:
        return cls(0, 255, 0)

    @classmethod
    def bright_yellow(cls) -> Color:
        return cls(255, 255, 0)

    @classmethod
    def bright_blue(cls) -> Color:
        return cls(92, 92, 255)

    @classmethod
    def bright_magenta(cls) -> Color:
        return cls(255, 0, 255)

    @classmethod
    def bright_cyan(cls) -> Color:
        return cls(0, 255, 255)

    @classmethod
    def bright_white(cls) -> Color:
        return cls(255, 255, 255)

    @classmethod
    def from_hex(cls, hex_str: str) -> Color:
        """Parse ``#RRGGBB`` or ``RRGGBB``."""
        h = hex_str.lstrip("#")
        return cls(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    @classmethod
    def from_256(cls, index: int) -> Color:
        """Convert a 256-colour index to an RGB :class:`Color`."""
        if index < 16:
            _basic = [
                (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
                (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
                (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
                (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
            ]
            r, g, b = _basic[index]
            return cls(r, g, b)
        if index < 232:
            index -= 16
            b_val = index % 6
            index //= 6
            g_val = index % 6
            r_val = index // 6
            return cls(
                r_val * 51 if r_val else 0,
                g_val * 51 if g_val else 0,
                b_val * 51 if b_val else 0,
            )
        # Greyscale 232..255
        v = (index - 232) * 10 + 8
        return cls(v, v, v)


@dataclass(frozen=True, slots=True)
class Style:
    """Visual style for a terminal cell.

    All attributes are optional; ``None`` means "inherit / default".
    """

    fg: Color | None = None
    bg: Color | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    dim: bool = False
    inverse: bool = False

    def merge(self, other: Style) -> Style:
        """Return a new style with *other*'s non-default values overlaid."""
        return Style(
            fg=other.fg if other.fg is not None else self.fg,
            bg=other.bg if other.bg is not None else self.bg,
            bold=other.bold or self.bold,
            italic=other.italic or self.italic,
            underline=other.underline or self.underline,
            strikethrough=other.strikethrough or self.strikethrough,
            dim=other.dim or self.dim,
            inverse=other.inverse or self.inverse,
        )


EMPTY_STYLE = Style()


# ---------------------------------------------------------------------------
# Cell
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Cell:
    """A single character cell in the screen buffer."""

    char: str = " "
    style: Style = field(default_factory=lambda: EMPTY_STYLE)

    @property
    def width(self) -> int:
        return char_width(self.char)


EMPTY_CELL = Cell()


# ---------------------------------------------------------------------------
# Screen Buffer
# ---------------------------------------------------------------------------

@dataclass
class ScreenBuffer:
    """A 2D grid of :class:`Cell` objects."""

    width: int
    height: int
    _cells: list[list[Cell]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self._cells:
            self._cells = [
                [EMPTY_CELL] * self.width for _ in range(self.height)
            ]

    def get(self, row: int, col: int) -> Cell:
        if 0 <= row < self.height and 0 <= col < self.width:
            return self._cells[row][col]
        return EMPTY_CELL

    def set(self, row: int, col: int, cell: Cell) -> None:
        if 0 <= row < self.height and 0 <= col < self.width:
            self._cells[row][col] = cell

    def write_text(
        self,
        row: int,
        col: int,
        text: str,
        style: Style | None = None,
    ) -> int:
        """Write *text* at (row, col) and return the column after the last written char.

        Wide characters occupy two columns; the second column is filled with a
        placeholder empty cell.
        """
        if row < 0 or row >= self.height:
            return col
        s = style or EMPTY_STYLE
        c = col
        for ch in text:
            if c >= self.width:
                break
            w = char_width(ch)
            if w == 0:
                continue
            if c + w > self.width:
                break
            self._cells[row][c] = Cell(char=ch, style=s)
            # Wide char: mark the next column as a continuation
            for offset in range(1, w):
                if c + offset < self.width:
                    self._cells[row][c + offset] = Cell(char="", style=s)
            c += w
        return c

    def fill(self, cell: Cell | None = None) -> None:
        """Fill every cell in the buffer with *cell* (defaults to empty)."""
        c = cell or EMPTY_CELL
        for row in range(self.height):
            for col in range(self.width):
                self._cells[row][col] = c

    def fill_region(
        self,
        row: int,
        col: int,
        width: int,
        height: int,
        cell: Cell | None = None,
    ) -> None:
        """Fill a rectangular region with *cell*."""
        c = cell or EMPTY_CELL
        for r in range(row, min(row + height, self.height)):
            if r < 0:
                continue
            for cc in range(col, min(col + width, self.width)):
                if cc < 0:
                    continue
                self._cells[r][cc] = c

    def resize(self, new_width: int, new_height: int) -> None:
        """Resize the buffer, preserving content where possible."""
        new_cells: list[list[Cell]] = []
        for r in range(new_height):
            row: list[Cell] = []
            for c in range(new_width):
                if r < self.height and c < self.width:
                    row.append(self._cells[r][c])
                else:
                    row.append(EMPTY_CELL)
            new_cells.append(row)
        self._cells = new_cells
        self.width = new_width
        self.height = new_height

    def clone(self) -> ScreenBuffer:
        """Return a deep copy of this buffer."""
        new_buf = ScreenBuffer(self.width, self.height)
        for r in range(self.height):
            for c in range(self.width):
                new_buf._cells[r][c] = self._cells[r][c]
        return new_buf


# ---------------------------------------------------------------------------
# ANSI sequence generation
# ---------------------------------------------------------------------------

def _sgr(style: Style) -> str:
    """Generate an SGR (Select Graphic Rendition) escape sequence for *style*."""
    parts: list[str] = ["0"]  # reset
    if style.bold:
        parts.append("1")
    if style.dim:
        parts.append("2")
    if style.italic:
        parts.append("3")
    if style.underline:
        parts.append("4")
    if style.inverse:
        parts.append("7")
    if style.strikethrough:
        parts.append("9")
    if style.fg is not None:
        parts.append(f"38;2;{style.fg.r};{style.fg.g};{style.fg.b}")
    if style.bg is not None:
        parts.append(f"48;2;{style.bg.r};{style.bg.g};{style.bg.b}")
    return f"\x1b[{';'.join(parts)}m"


def _move_cursor(row: int, col: int) -> str:
    """Generate a CUP (cursor position) escape sequence.  Rows/cols are 1-based."""
    return f"\x1b[{row + 1};{col + 1}H"


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------

@dataclass
class Terminal:
    """Raw terminal interaction and differential rendering engine.

    Usage::

        term = Terminal()
        term.enter()
        try:
            buf = term.back_buffer
            buf.write_text(0, 0, "Hello, world!", Style(fg=Color.green()))
            term.present()
        finally:
            term.leave()
    """

    _out: TextIO = field(default_factory=lambda: sys.stdout)
    _width: int = 0
    _height: int = 0
    _front: ScreenBuffer | None = field(default=None, repr=False)
    _back: ScreenBuffer | None = field(default=None, repr=False)
    _cursor_visible: bool = True
    _alternate_screen: bool = False
    _resize_callbacks: list[Callable[[int, int], None]] = field(default_factory=list)

    # -- lifecycle ------------------------------------------------------------

    def enter(self, *, alternate_screen: bool = True) -> None:
        """Prepare the terminal for TUI rendering."""
        self._detect_size()
        self._front = ScreenBuffer(self._width, self._height)
        self._back = ScreenBuffer(self._width, self._height)
        if alternate_screen:
            self._write("\x1b[?1049h")  # enter alt screen
            self._alternate_screen = True
        self.hide_cursor()
        self._write("\x1b[2J")  # clear screen
        signal.signal(signal.SIGWINCH, self._handle_sigwinch)

    def leave(self) -> None:
        """Restore the terminal to its previous state."""
        self.show_cursor()
        self._write("\x1b[0m")  # reset style
        if self._alternate_screen:
            self._write("\x1b[?1049l")  # leave alt screen
            self._alternate_screen = False
        self._flush()
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)

    # -- size -----------------------------------------------------------------

    def _detect_size(self) -> None:
        try:
            size = os.get_terminal_size()
            self._width = size.columns
            self._height = size.lines
        except OSError:
            self._width = 80
            self._height = 24

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def _handle_sigwinch(self, signum: int, frame: object) -> None:
        old_w, old_h = self._width, self._height
        self._detect_size()
        if self._front is not None:
            self._front.resize(self._width, self._height)
        if self._back is not None:
            self._back.resize(self._width, self._height)
        if (old_w, old_h) != (self._width, self._height):
            for cb in self._resize_callbacks:
                cb(self._width, self._height)

    def on_resize(self, callback: Callable[[int, int], None]) -> None:
        """Register a callback ``(width, height) -> None`` for resize events."""
        self._resize_callbacks.append(callback)

    # -- buffers --------------------------------------------------------------

    @property
    def back_buffer(self) -> ScreenBuffer:
        """The buffer to draw into. Changes appear on screen after :meth:`present`."""
        assert self._back is not None, "Terminal.enter() must be called first"
        return self._back

    def clear_back_buffer(self) -> None:
        """Reset the back buffer to empty cells."""
        if self._back is not None:
            self._back.fill()

    # -- rendering ------------------------------------------------------------

    def present(self) -> None:
        """Diff the back buffer against the front buffer and flush changes."""
        assert self._front is not None and self._back is not None
        out_parts: list[str] = []
        prev_style: Style | None = None

        for row in range(self._height):
            for col in range(self._width):
                new_cell = self._back.get(row, col)
                old_cell = self._front.get(row, col)
                if new_cell == old_cell:
                    continue

                # Position cursor
                out_parts.append(_move_cursor(row, col))

                # Apply style if changed
                if new_cell.style != prev_style:
                    out_parts.append(_sgr(new_cell.style))
                    prev_style = new_cell.style

                ch = new_cell.char
                if ch == "":
                    # Continuation cell for wide char -- skip
                    continue
                out_parts.append(ch if ch else " ")

        if out_parts:
            self._write("".join(out_parts))
            self._flush()

        # Swap: front = copy of back
        self._front = self._back.clone()

    def force_redraw(self) -> None:
        """Force a full redraw on the next :meth:`present` by invalidating the front buffer."""
        if self._front is not None:
            self._front.fill()

    # -- cursor ---------------------------------------------------------------

    def hide_cursor(self) -> None:
        if self._cursor_visible:
            self._write("\x1b[?25l")
            self._cursor_visible = False

    def show_cursor(self) -> None:
        if not self._cursor_visible:
            self._write("\x1b[?25h")
            self._cursor_visible = True

    def move_cursor(self, row: int, col: int) -> None:
        """Move the cursor to the given position."""
        self._write(_move_cursor(row, col))
        self._flush()

    # -- low-level I/O --------------------------------------------------------

    def _write(self, data: str) -> None:
        self._out.write(data)

    def _flush(self) -> None:
        self._out.flush()

    def write_raw(self, data: str) -> None:
        """Write raw data directly to the output stream (bypass buffering)."""
        self._write(data)
        self._flush()
