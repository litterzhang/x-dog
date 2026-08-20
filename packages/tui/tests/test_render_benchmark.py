"""Non-gating render churn benchmark helpers."""

from time import perf_counter

from xdog.tui.components.text import Text
from xdog.tui.tui import TUI


def test_large_transcript_render_stays_interactive() -> None:
    tui = TUI()
    for index in range(500):
        tui.add_child(Text(f"message {index}"))

    started = perf_counter()
    lines = tui.render(100)
    elapsed = perf_counter() - started

    assert len(lines) >= 500
    assert elapsed < 0.5
