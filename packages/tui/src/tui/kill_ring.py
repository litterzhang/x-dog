"""Kill ring -- an Emacs-style clipboard ring.

Stores killed (cut) text entries in a circular buffer.  Successive yanks cycle
through older entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KillRing:
    """An Emacs-style kill ring (circular clipboard).

    Attributes:
        max_size: Maximum number of entries in the ring.
    """

    max_size: int = 60
    _entries: list[str] = field(default_factory=list)
    _yank_index: int = -1

    # -- mutators -------------------------------------------------------------

    def kill(self, text: str) -> None:
        """Push *text* onto the kill ring."""
        if not text:
            return
        self._entries.append(text)
        if len(self._entries) > self.max_size:
            self._entries = self._entries[-self.max_size :]
        # Reset yank pointer to most recent
        self._yank_index = len(self._entries) - 1

    def append_kill(self, text: str) -> None:
        """Append *text* to the most recent kill ring entry.

        Used for successive kill commands (e.g. multiple ``ctrl-k``).
        """
        if not text:
            return
        if self._entries:
            self._entries[-1] = self._entries[-1] + text
        else:
            self.kill(text)

    # -- accessors ------------------------------------------------------------

    def yank(self) -> str | None:
        """Return the most recent kill ring entry, or ``None`` if empty."""
        if not self._entries:
            return None
        self._yank_index = len(self._entries) - 1
        return self._entries[self._yank_index]

    def yank_pop(self) -> str | None:
        """Cycle to the previous kill ring entry.

        Should be called after :meth:`yank` to rotate through older entries.
        Returns ``None`` if the ring is empty.
        """
        if not self._entries:
            return None
        self._yank_index = (self._yank_index - 1) % len(self._entries)
        return self._entries[self._yank_index]

    # -- query ----------------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return len(self._entries) == 0

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[str]:
        """Return a copy of all entries (oldest first)."""
        return list(self._entries)

    def clear(self) -> None:
        """Remove all entries from the ring."""
        self._entries.clear()
        self._yank_index = -1
