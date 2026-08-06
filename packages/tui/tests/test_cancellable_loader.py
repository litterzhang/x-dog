"""Tests for tui.components.cancellable_loader — loader with Escape cancellation."""

from xdog.tui.components.cancellable_loader import CancellableLoader
from xdog.tui.keys import KeyEvent


def _make_loader() -> CancellableLoader:
    return CancellableLoader(
        tui=None,
        spinner_fn=lambda s: s,
        message_fn=lambda s: s,
        message="Working...",
    )

def test_cancel_on_escape():
    """Pressing Escape sets the cancel event."""
    loader = _make_loader()
    assert not loader.cancelled

    loader.handle_input(KeyEvent(key="escape"))
    assert loader.cancelled
    assert loader.cancel_event.is_set()
    loader.stop()

def test_abort_callback():
    """on_abort is called when Escape is pressed."""
    loader = _make_loader()
    aborted = []
    loader.on_abort = lambda: aborted.append(True)

    loader.handle_input(KeyEvent(key="escape"))
    assert aborted == [True]
    loader.stop()

