"""Stdin buffering for non-blocking terminal input.

Wraps ``sys.stdin`` with raw-mode handling and provides a buffer that
accumulates bytes between reads.  Includes escape sequence boundary
detection so partial sequences are not emitted prematurely.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
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

    def enter_raw(self) -> None:
        """Switch stdin to raw mode, saving the original terminal settings."""
        self._fd = sys.stdin.fileno()
        self._original_termios = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)

    def restore(self) -> None:
        """Restore the original terminal settings."""
        if self._original_termios is not None and self._fd >= 0:
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
