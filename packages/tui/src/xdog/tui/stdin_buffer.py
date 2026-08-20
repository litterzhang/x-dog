"""Stdin buffering for non-blocking terminal input.

Wraps ``sys.stdin`` with raw-mode handling and provides a buffer that
accumulates bytes between reads.  Includes escape sequence boundary
detection so partial sequences are not emitted prematurely.
"""

from __future__ import annotations

import os
import select
import sys
import time

try:
    import termios
    import tty
except ImportError:  # Windows
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]
from dataclasses import dataclass, field
from typing import Any, Callable


def is_complete_sequence(data: bytes) -> bool:
    """Return ``True`` if *data* contains only complete escape sequences.

    Incomplete CSI, OSC, DCS, APC, SS2/SS3, or Kitty sequences cause
    ``False`` to be returned so the caller can wait for more bytes.
    """
    i = 0
    length = len(data)

    while i < length:
        b = data[i]

        if b != 0x1B:
            # Regular byte — always complete
            i += 1
            continue

        # ESC at end of buffer — incomplete
        if i + 1 >= length:
            return False

        next_b = data[i + 1]

        # CSI: ESC [
        if next_b == 0x5B:  # '['
            i += 2
            # Scan for final byte (0x40–0x7E)
            while i < length:
                if 0x40 <= data[i] <= 0x7E:
                    i += 1
                    break
                i += 1
            else:
                return False  # Ran off end — incomplete
            continue

        # SS3: ESC O
        if next_b == 0x4F:  # 'O'
            if i + 2 >= length:
                return False
            i += 3  # ESC O <char>
            continue

        # SS2: ESC N
        if next_b == 0x4E:  # 'N'
            if i + 2 >= length:
                return False
            i += 3
            continue

        # OSC: ESC ]  — terminated by BEL (0x07) or ST (ESC \)
        if next_b == 0x5D:  # ']'
            i += 2
            while i < length:
                if data[i] == 0x07:  # BEL
                    i += 1
                    break
                if data[i] == 0x1B and i + 1 < length and data[i + 1] == 0x5C:  # ST
                    i += 2
                    break
                i += 1
            else:
                return False
            continue

        # DCS: ESC P  — terminated by ST (ESC \)
        if next_b == 0x50:  # 'P'
            i += 2
            while i < length:
                if data[i] == 0x1B and i + 1 < length and data[i + 1] == 0x5C:
                    i += 2
                    break
                i += 1
            else:
                return False
            continue

        # APC: ESC _  — terminated by BEL or ST
        if next_b == 0x5F:  # '_'
            i += 2
            while i < length:
                if data[i] == 0x07:
                    i += 1
                    break
                if data[i] == 0x1B and i + 1 < length and data[i + 1] == 0x5C:
                    i += 2
                    break
                i += 1
            else:
                return False
            continue

        # ESC + printable — Alt+key (2 bytes total)
        if 0x20 <= next_b < 0x7F:
            i += 2
            continue

        # ESC + control — Alt+Ctrl+key
        if next_b < 0x20:
            i += 2
            continue

        # Unknown ESC sequence — treat as bare ESC
        i += 1

    return True


@dataclass(frozen=True, slots=True)
class KeyBytes:
    data: bytes


@dataclass(frozen=True, slots=True)
class Paste:
    text: str


InputFrame = KeyBytes | Paste


@dataclass
class StdinBuffer:
    """Non-blocking stdin reader with buffering and sequence detection.

    Call :meth:`enter_raw` to switch the terminal into raw mode and
    :meth:`restore` to return to the original settings.
    """

    _original_termios: Any = field(default=None, repr=False)
    _buffer: bytearray = field(default_factory=bytearray)
    _fd: int = field(default=-1)
    on_data: Callable[[bytes], None] | None = field(default=None, repr=False)
    _paste: bytearray | None = field(default=None, repr=False)
    _pending_since: float | None = field(default=None, repr=False)

    def feed(self, data: bytes) -> list[InputFrame]:
        """Incrementally frame key bytes and atomic bracketed paste payloads."""
        self._buffer.extend(data)
        if self._buffer and self._pending_since is None:
            self._pending_since = time.monotonic()
        frames: list[InputFrame] = []
        paste_start = b"\x1b[200~"
        paste_end = b"\x1b[201~"

        while self._buffer:
            if self._paste is not None:
                end = self._buffer.find(paste_end)
                if end < 0:
                    # Preserve a possible split terminator suffix.
                    keep = min(len(paste_end) - 1, len(self._buffer))
                    if len(self._buffer) > keep:
                        self._paste.extend(self._buffer[:-keep])
                        del self._buffer[:-keep]
                    break
                self._paste.extend(self._buffer[:end])
                del self._buffer[:end + len(paste_end)]
                frames.append(Paste(self._paste.decode("utf-8", errors="replace")))
                self._paste = None
                continue

            start = self._buffer.find(paste_start)
            if start >= 0:
                if start:
                    frames.extend(self._emit_complete_prefix(start))
                    if self._buffer and self._buffer.find(paste_start) != 0:
                        break
                if self._buffer.startswith(paste_start):
                    del self._buffer[:len(paste_start)]
                    self._paste = bytearray()
                    continue

            if paste_start.startswith(bytes(self._buffer)):
                break
            frames.extend(self._emit_complete_prefix(len(self._buffer)))
            break

        if not self._buffer:
            self._pending_since = None
        return frames

    def flush_expired(
        self,
        *,
        now: float | None = None,
        escape_timeout: float | None = None,
        sequence_timeout: float = 0.05,
    ) -> list[InputFrame]:
        """Flush a stalled partial sequence after terminal/SSH-safe timeout."""
        if not self._buffer or self._pending_since is None:
            return []
        current = time.monotonic() if now is None else now
        esc_timeout = (
            0.1 if escape_timeout is None and os.environ.get("SSH_CONNECTION")
            else (0.01 if escape_timeout is None else escape_timeout)
        )
        timeout = esc_timeout if self._buffer == b"\x1b" else sequence_timeout
        if current - self._pending_since < timeout:
            return []
        data = bytes(self._buffer)
        self._buffer.clear()
        self._pending_since = None
        return [KeyBytes(data)]

    def _emit_complete_prefix(self, limit: int) -> list[InputFrame]:
        candidate = bytes(self._buffer[:limit])
        if not candidate:
            return []
        # UTF-8 and terminal escape sequences must remain buffered until whole.
        try:
            candidate.decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.reason == "unexpected end of data":
                return []
        if not is_complete_sequence(candidate):
            return []
        del self._buffer[:limit]
        return [KeyBytes(candidate)]

    def enter_raw(self) -> None:
        """Switch stdin to raw mode, saving the original terminal settings."""
        self._fd = sys.stdin.fileno()
        if termios is None or tty is None:
            return
        self._original_termios = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)

    def restore(self) -> None:
        """Restore the original terminal settings."""
        if self._original_termios is not None and self._fd >= 0 and termios is not None:
            termios.tcsetattr(self._fd, termios.TCSAFLUSH, self._original_termios)
            self._original_termios = None

    def read(self, timeout: float = 0.0) -> bytes:
        """Read available bytes from stdin.

        Args:
            timeout: Seconds to wait for data.  ``0`` means non-blocking.
                     Negative means block indefinitely.

        Returns:
            Raw bytes read from stdin (may be empty if nothing was available
            within the timeout).
        """
        fd = self._fd if self._fd >= 0 else sys.stdin.fileno()

        if timeout < 0:
            ready, _, _ = select.select([fd], [], [])
        elif timeout == 0:
            ready, _, _ = select.select([fd], [], [], 0)
        else:
            ready, _, _ = select.select([fd], [], [], timeout)

        if not ready:
            return b""

        data = os.read(fd, 4096)
        return data

    def read_buffered(self, timeout: float = 0.0) -> bytes:
        """Read bytes and append to internal buffer, returning the full buffer.

        The internal buffer is cleared after this call so callers get a
        complete snapshot of accumulated input.
        """
        new_data = self.read(timeout)
        if new_data:
            self._buffer.extend(new_data)
        result = bytes(self._buffer)
        self._buffer.clear()
        return result

    def read_complete(self, timeout: float = 0.0, wait: float = 0.005) -> bytes:
        """Read bytes, waiting for complete escape sequences.

        First reads with *timeout*, then drains with short *wait* intervals
        until the accumulated buffer contains only complete sequences.
        """
        data = self.read(timeout)
        if not data:
            return b""

        self._buffer.extend(data)

        # Keep reading while the buffer has incomplete sequences
        while not is_complete_sequence(bytes(self._buffer)):
            more = self.read(wait)
            if not more:
                break
            self._buffer.extend(more)

        result = bytes(self._buffer)
        self._buffer.clear()

        if self.on_data is not None:
            self.on_data(result)

        return result

    def has_data(self, timeout: float = 0.0) -> bool:
        """Return ``True`` if there is data waiting on stdin."""
        fd = self._fd if self._fd >= 0 else sys.stdin.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        return bool(ready)

    @property
    def is_raw(self) -> bool:
        """Return ``True`` if the terminal is currently in raw mode."""
        return self._original_termios is not None
