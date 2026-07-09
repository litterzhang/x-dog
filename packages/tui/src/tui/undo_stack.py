"""Generic undo/redo stack with configurable depth.

Each entry stores an immutable snapshot that the caller defines (typically a
dataclass capturing the relevant editor state).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class UndoStack(Generic[T]):
    """An undo/redo stack that stores snapshots of type *T*.

    Attributes:
        max_size: Maximum number of undo entries to retain.
    """

    max_size: int = 200
    _undo: list[T] = field(default_factory=list)
    _redo: list[T] = field(default_factory=list)

    # -- public API -----------------------------------------------------------

    def push(self, state: T) -> None:
        """Record *state* as a new undo point.

        Clears any redo history (the timeline has forked).
        """
        self._undo.append(state)
        if len(self._undo) > self.max_size:
            self._undo = self._undo[-self.max_size :]
        self._redo.clear()

    def undo(self, current: T) -> T | None:
        """Move back one step.

        *current* is the live state **before** the undo -- it is pushed onto
        the redo stack so that :meth:`redo` can restore it.

        Returns the previous state, or ``None`` if there is nothing to undo.
        """
        if not self._undo:
            return None
        self._redo.append(current)
        return self._undo.pop()

    def redo(self, current: T) -> T | None:
        """Move forward one step after an undo.

        Returns the restored state, or ``None`` if there is nothing to redo.
        """
        if not self._redo:
            return None
        self._undo.append(current)
        return self._redo.pop()

    # -- query ----------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return len(self._undo) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo) > 0

    @property
    def undo_depth(self) -> int:
        return len(self._undo)

    @property
    def redo_depth(self) -> int:
        return len(self._redo)

    def clear(self) -> None:
        """Discard all undo and redo history."""
        self._undo.clear()
        self._redo.clear()
