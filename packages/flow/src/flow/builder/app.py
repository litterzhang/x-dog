"""flow.builder.app — terminal-facing shell wiring BuilderModel + actions to tui.

This module is the thin, terminal-touching layer over the headless
:class:`~flow.builder.model.BuilderModel` and the pure functions in
:mod:`flow.builder.actions`.  It translates single-char / arrow
:class:`~tui.keys.KeyEvent` presses into action calls, renders the current node
list plus a validation status line, and offers a blocking :func:`run` that
mounts the app in a real :class:`~tui.tui.TUI`.

Every state transition goes through the pure action functions, so the app stays
trivially unit-testable by feeding synthetic ``KeyEvent`` objects.  Only
:func:`run` touches the terminal and is excluded from coverage.
"""

from __future__ import annotations

import pathlib

from tui.keys import KeyEvent
from tui.tui import TUI, Component

from flow.builder import actions
from flow.builder.io import dump_any, load_any
from flow.builder.model import BuilderModel, empty_model, model_from_workflow
from flow.graph import to_ascii


def _fit(line: str, width: int) -> str:
    """Truncate/pad *line* to exactly *width* columns (never negative width)."""
    if width <= 0:
        return ""
    if len(line) > width:
        return line[:width]
    return line + " " * (width - len(line))


class BuilderApp(Component):
    """A :class:`~tui.tui.Component` shell over a headless builder model."""

    def __init__(self, model: BuilderModel, path: pathlib.Path) -> None:
        self._model = model
        self._path = path
        self._mode = "normal"
        self._buf = ""
        self._edge_src: str | None = None

    @property
    def model(self) -> BuilderModel:
        """The current :class:`~flow.builder.model.BuilderModel`."""
        return self._model

    def render(self, width: int) -> list[str]:
        model = self._model
        lines: list[str] = [f"builder: {model.wf.name}"]
        for node_id in model.node_ids:
            if node_id == model.selected:
                marker = ">"
            elif self._mode == "edge" and node_id == self._edge_src:
                marker = "*"
            else:
                marker = " "
            lines.append(f"{marker} {node_id}")
        lines.extend(to_ascii(model.wf).splitlines())
        status = "valid" if model.error is None else model.error
        lines.append(f"status: {status}")
        lines.append(f"mode: {self._mode}")
        return [_fit(line, width) for line in lines]

    def handle_input(self, event: KeyEvent) -> bool:
        if self._mode == "prompt":
            return self._handle_prompt(event)
        if self._mode == "edge":
            return self._handle_edge(event)
        return self._handle_normal(event)

    # -- normal mode -----------------------------------------------------------

    def _handle_normal(self, event: KeyEvent) -> bool:
        key = event.key
        model = self._model
        if key == "a":
            self._model = actions.add_node(model, "agent")
            return True
        if key == "s":
            self._model = actions.add_node(model, "script")
            return True
        if key in ("j", "down"):
            self._move(1)
            return True
        if key in ("k", "up"):
            self._move(-1)
            return True
        if key == "d":
            if model.selected is not None:
                self._model = actions.remove_node(model, model.selected)
            return True
        if key == "w":
            self._save()
            return True
        if key == "p":
            if model.selected is not None:
                self._mode = "prompt"
                self._buf = self._selected_prompt()
            return True
        if key == "e":
            if model.selected is not None:
                self._mode = "edge"
                self._edge_src = model.selected
            return True
        return False

    # -- prompt-edit mode ------------------------------------------------------

    def _handle_prompt(self, event: KeyEvent) -> bool:
        key = event.key
        if key == "enter":
            selected = self._model.selected
            if selected is not None:
                self._model = actions.set_field(self._model, selected, "prompt", self._buf)
            self._mode = "normal"
            return True
        if key == "escape":
            self._buf = ""
            self._mode = "normal"
            return True
        if key == "backspace":
            self._buf = self._buf[:-1]
            return True
        if len(key) == 1:
            self._buf += key
            return True
        return True

    # -- edge mode -------------------------------------------------------------

    def _handle_edge(self, event: KeyEvent) -> bool:
        key = event.key
        if key in ("j", "down"):
            self._move(1)
            return True
        if key in ("k", "up"):
            self._move(-1)
            return True
        if key == "enter":
            src = self._edge_src
            dst = self._model.selected
            if src is not None and dst is not None:
                self._model = actions.add_edge(self._model, src, dst)
            self._edge_src = None
            self._mode = "normal"
            return True
        if key == "escape":
            self._edge_src = None
            self._mode = "normal"
            return True
        return True

    # -- helpers ---------------------------------------------------------------

    def _selected_prompt(self) -> str:
        selected = self._model.selected
        for node in self._model.wf.nodes:
            if node.id == selected:
                return node.prompt
        return ""

    def _move(self, delta: int) -> None:
        ids = self._model.node_ids
        if not ids:
            return
        idx = ids.index(self._model.selected) if self._model.selected in ids else 0
        new_idx = max(0, min(len(ids) - 1, idx + delta))
        self._model = actions.select(self._model, ids[new_idx])

    def _save(self) -> None:
        if self._model.error is None:
            dump_any(self._model.wf, self._path)
            self._model = model_from_workflow(self._model.wf)


def build_app(path: str | pathlib.Path) -> BuilderApp:
    """Build a :class:`BuilderApp`, loading *path* if it exists else starting empty."""
    p = pathlib.Path(path)
    if p.exists():
        model = model_from_workflow(load_any(p))
    else:
        model = empty_model(name=p.stem)
    return BuilderApp(model, p)


def run(path: str | pathlib.Path) -> None:  # pragma: no cover
    """Build the app and run it in a real terminal (blocking)."""
    app = build_app(path)
    tui = TUI()
    tui.add_child(app)
    tui.set_focus(app)
    try:
        tui.start()
    finally:
        tui.stop()
