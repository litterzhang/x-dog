"""flow.builder.app — a terminal-free-testable TUI shell for the workflow builder.

``BuilderApp`` wraps a headless :class:`~flow.builder.model.BuilderModel`, applies
pure edit operations from :mod:`flow.builder.actions` in response to key events,
and renders a node list plus a validation status line.  No terminal I/O happens
outside :func:`run`, so all key bindings and rendering are unit-testable by
feeding synthetic :class:`~tui.keys.KeyEvent` objects.
"""

from __future__ import annotations

import pathlib

from tui.keys import KeyEvent
from tui.tui import TUI, Component

from flow.builder import actions
from flow.builder.model import BuilderModel, empty_model, model_from_workflow
from flow.graph import to_ascii
from flow.loader import load_workflow


class BuilderApp(Component):
    """A headless, unit-testable TUI shell around a :class:`BuilderModel`."""

    def __init__(self, model: BuilderModel) -> None:
        self._model = model

    @property
    def model(self) -> BuilderModel:
        """The current (immutable) builder model."""
        return self._model

    def handle_input(self, event: KeyEvent) -> bool:
        key = event.key
        if key == "a":
            self._model = actions.add_node(self._model, "agent")
            return True
        if key == "s":
            self._model = actions.add_node(self._model, "script")
            return True
        if key in ("j", "down"):
            self._move(1)
            return True
        if key in ("k", "up"):
            self._move(-1)
            return True
        if key == "d":
            if self._model.selected is not None:
                self._model = actions.remove_node(self._model, self._model.selected)
            return True
        return False

    def _move(self, delta: int) -> None:
        node_ids = self._model.node_ids
        if not node_ids:
            return
        try:
            index = node_ids.index(self._model.selected) if self._model.selected is not None else 0
        except ValueError:
            index = 0
        new_index = max(0, min(len(node_ids) - 1, index + delta))
        self._model = actions.select(self._model, node_ids[new_index])

    def render(self, width: int) -> list[str]:
        lines: list[str] = [f"workflow: {self._model.wf.name}"]
        for node_id in self._model.node_ids:
            prefix = "> " if node_id == self._model.selected else "  "
            lines.append(f"{prefix}{node_id}")
        status = "valid" if self._model.error is None else self._model.error
        lines.append(f"status: {status}")
        if self._model.wf.nodes:
            preview = to_ascii(self._model.wf)
            lines.extend(preview.splitlines())
        return lines


def build_app(path: str | pathlib.Path) -> BuilderApp:
    """Load the workflow at *path* if it exists, else start an empty model."""
    path = pathlib.Path(path)
    if path.exists():
        model = model_from_workflow(load_workflow(path))
    else:
        model = empty_model(name=path.stem)
    return BuilderApp(model)


def run(path: str | pathlib.Path) -> None:  # pragma: no cover
    """Build the app, mount it in a TUI and start the blocking loop."""
    app = build_app(path)
    tui = TUI()
    try:
        tui.add_child(app)
        tui.set_focus(app)
        tui.start()
    finally:
        tui.stop()
