"""Cancellable loader -- loader that can be cancelled with Escape.

Extends :class:`~tui.components.loader.Loader` with an
:class:`asyncio.Event` that is set when the user presses Escape,
allowing the caller to abort the associated async operation.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from xdog.tui.components.loader import Loader
from xdog.tui.keys import KeyEvent


class CancellableLoader(Loader):
    """Loader that can be cancelled by pressing Escape.

    Example::

        loader = CancellableLoader(tui, spinner_fn, msg_fn, "Working...")
        loader.on_abort = lambda: handle_cancel()
        await do_work(loader.cancel_event)
    """

    def __init__(
        self,
        tui: object,
        spinner_fn: Callable[[str], str],
        message_fn: Callable[[str], str],
        message: str = "Loading...",
    ) -> None:
        super().__init__(tui, spinner_fn, message_fn, message)
        self._cancel_event = asyncio.Event()
        self.on_abort: Callable[[], None] | None = None

    @property
    def cancelled(self) -> bool:
        """Whether the loader was cancelled."""
        return self._cancel_event.is_set()

    @property
    def cancel_event(self) -> asyncio.Event:
        """Event that is set when the user presses Escape."""
        return self._cancel_event

    def handle_input(self, event: KeyEvent) -> None:  # type: ignore[override]
        """Cancel on Escape key."""
        if event.matches("escape"):
            self._cancel_event.set()
            if self.on_abort is not None:
                self.on_abort()

    def dispose(self) -> None:
        """Stop the spinner and clean up."""
        self.stop()
