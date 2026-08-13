"""Regression tests for the main-buffer differential renderer."""

from __future__ import annotations

import io

from xdog.tui.components.text import Text
from xdog.tui.tui import TUI, Component, OverlayOptions


class _MutableLines(Component):
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def render(self, width: int) -> list[str]:
        return list(self.lines)


def test_full_redraw_preserves_terminal_scrollback(monkeypatch) -> None:
    import xdog.tui.tui as tui_module

    output = io.StringIO()
    monkeypatch.setattr(tui_module.sys, "stdout", output)
    monkeypatch.setattr(tui_module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(tui_module, "_terminal_height", lambda: 24)

    component = _MutableLines(["one", "two", "three"])
    tui = TUI()
    tui.add_child(component)
    tui._do_render()

    output.seek(0)
    output.truncate(0)
    component.lines = ["one"]  # shrinking content takes the full-redraw path
    tui._do_render()

    rendered = output.getvalue()
    assert "\x1b[2J" not in rendered
    assert "\x1b[3J" not in rendered
    assert "\x1b[2K" in rendered
    # Repainting 24 rows uses 23 line feeds and never scrolls at the bottom.
    assert rendered.count("\r\n") == 23


def test_full_redraw_does_not_replay_offscreen_history(monkeypatch) -> None:
    import xdog.tui.tui as tui_module

    output = io.StringIO()
    monkeypatch.setattr(tui_module.sys, "stdout", output)
    monkeypatch.setattr(tui_module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(tui_module, "_terminal_height", lambda: 3)

    component = _MutableLines([f"history-{index}" for index in range(6)])
    tui = TUI()
    tui.add_child(component)
    tui._do_render()

    output.seek(0)
    output.truncate(0)
    # A change above the viewport takes the clear/full-redraw path.
    component.lines[0] = "changed-offscreen-history"
    tui._do_render()

    rendered = output.getvalue()
    assert "\x1b[2J" not in rendered
    assert rendered.count("\r\n") == 2
    assert "changed-offscreen-history" not in rendered
    assert "history-3" in rendered
    assert "history-5" in rendered


def test_shrinking_transient_panel_does_not_replay_input_box(monkeypatch) -> None:
    import xdog.tui.tui as tui_module

    output = io.StringIO()
    monkeypatch.setattr(tui_module.sys, "stdout", output)
    monkeypatch.setattr(tui_module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(tui_module, "_terminal_height", lambda: 4)

    component = _MutableLines([
        "chat",
        "status",
        "footer",
        "> input",
        "permission choice 1",
        "permission choice 2",
    ])
    tui = TUI()
    tui.add_child(component)
    tui._do_render()

    output.seek(0)
    output.truncate(0)
    component.lines = ["chat", "status", "footer", "> input"]
    tui._do_render()

    rendered = output.getvalue()
    assert rendered.count("> input") == 1
    assert rendered.count("\r\n") == 3
    assert "\x1b[2J" not in rendered


def test_overlay_is_composited_into_visible_tail_viewport() -> None:
    tui = TUI()
    base_lines = [f"history-{index}" for index in range(30)]
    tui.show_overlay(
        Text("PERMISSION DIALOG", 0, 0),
        OverlayOptions(width=24, anchor="center"),
    )

    composited = tui._composite_overlays(base_lines, width=80, height=10)

    assert not any("PERMISSION DIALOG" in line for line in composited[:-10])
    assert any("PERMISSION DIALOG" in line for line in composited[-10:])
