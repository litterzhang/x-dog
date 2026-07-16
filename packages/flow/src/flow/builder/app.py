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
from collections.abc import Callable

from tui.keys import KeyEvent
from tui.tui import TUI, Component
from tui.utils import visible_width

from flow.builder import actions, style
from flow.builder.io import dump_any, load_any
from flow.builder.model import BuilderModel, empty_model, model_from_workflow
from flow.graph import to_ascii_diagram

_MIN_WIDTH = 60
_LEFT_MAX = 44
_SEP = "  │  "
_KEYHINT = "a/s add   j/k move   d del   p prompt   e edge   w save   q quit"


class BuilderApp(Component):
    """A :class:`~tui.tui.Component` shell over a headless builder model."""

    def __init__(self, model: BuilderModel, path: pathlib.Path) -> None:
        self._model = model
        self._path = path
        self._mode = "normal"
        self._buf = ""
        self._edge_src: str | None = None
        self._message = ""
        self.on_quit: Callable[[], None] | None = None

    @property
    def model(self) -> BuilderModel:
        """The current :class:`~flow.builder.model.BuilderModel`."""
        return self._model

    def render(self, width: int) -> list[str]:
        # Guard against zero/negative widths (a pty can report width 0), so the
        # UI never collapses to all-empty lines.
        w = max(width, _MIN_WIDTH)
        left_w = min(_LEFT_MAX, max(20, w * 2 // 5))
        right_w = max(20, w - left_w - visible_width(_SEP))
        model = self._model

        left = self._left_column(left_w)
        right = self._right_column(right_w)

        out: list[str] = []
        # Title bar.
        title = style.bold(style.fg(f" flow builder — {model.wf.name} ", style.TITLE))
        out.append(style.pad(title, w))
        out.append(style.dim("─" * w))
        # Zip the two columns row by row.
        rows = max(len(left), len(right))
        sep = style.dim(_SEP)
        for i in range(rows):
            lcell = left[i] if i < len(left) else ""
            rcell = right[i] if i < len(right) else ""
            out.append(style.pad(style.pad(lcell, left_w) + sep + style.pad(rcell, right_w), w))
        # Footer: status + mode + keyhint.
        out.append(style.dim("─" * w))
        if model.error is None:
            status = style.fg("● valid", style.OK)
        else:
            status = style.fg(f"✗ {model.error}", style.ERR)
        mode = style.dim(f"[{self._mode}]")
        out.append(style.pad(f"{status}   {mode}", w))
        if self._mode == "prompt":
            out.append(style.pad(style.fg(f"prompt> {self._buf}", style.ENTRY), w))
        if self._message:
            out.append(style.pad(style.dim(self._message), w))
        out.append(style.pad(style.dim(_KEYHINT), w))
        return [style.pad(line, w) for line in out]

    def _left_column(self, w: int) -> list[str]:
        model = self._model
        lines: list[str] = [style.bold("NODES")]
        if model.node_ids:
            for node in model.wf.nodes:
                accent = style.AGENT if node.type == "agent" else style.SCRIPT
                tag = style.fg(f"[{node.type}]", accent)
                entry = style.fg(" *", style.ENTRY) if node.id == model.wf.entry else ""
                selected = node.id == model.selected
                edge_src = self._mode == "edge" and node.id == self._edge_src
                marker = "▸ " if selected else ("◆ " if edge_src else "  ")
                row = f"{marker}{node.id} {tag}{entry}"
                if selected:
                    row = style.bg(style.pad(row, w), style.SELECT_BG)
                lines.append(row)
        else:
            lines.append(style.dim("  (press 'a' to add a node)"))
        lines.append(style.dim("─" * w))
        lines.append(style.bold("DETAILS"))
        lines.extend(self._detail_lines())
        return lines

    def _right_column(self, w: int) -> list[str]:
        lines: list[str] = [style.bold("GRAPH")]
        for gl in to_ascii_diagram(self._model.wf).splitlines():
            stripped = gl.strip()
            if stripped.startswith(("┌", "└", "│")):
                lines.append(style.dim(gl))
            elif "↺" in gl:
                lines.append(style.fg(gl, style.ERR))
            elif gl.strip() in ("│", "▼") or "├─" in gl:
                lines.append(style.fg(gl, style.MUTED))
            else:
                lines.append(gl)
        return lines

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
        if key == "q":
            if self.on_quit is not None:
                self.on_quit()
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

    def _detail_lines(self) -> list[str]:
        """Field-by-field detail of the currently selected node (dim labels)."""
        selected = self._model.selected
        node = next((n for n in self._model.wf.nodes if n.id == selected), None)
        if node is None:
            return [style.dim("  (no node selected)")]

        def row(label: str, value: str) -> str:
            return "  " + style.dim(f"{label:<8}") + value

        out: list[str] = [row("id", node.id), row("type", node.type)]
        if node.type == "script":
            out.append(row("run", node.run or "(unset)"))
        else:
            out.append(row("model", node.model or "(default)"))
            if node.system_prompt:
                out.append(row("system", node.system_prompt))
            if node.prompt:
                out.append(row("prompt", node.prompt))
            if node.tools:
                out.append(row("tools", ", ".join(node.tools)))
            if node.output_schema:
                fields = ", ".join(f"{k}:{v}" for k, v in node.output_schema)
                out.append(row("schema", fields))
        if node.inputs:
            out.append(row("inputs", ", ".join(node.inputs)))
        out.append(row("output", node.output or "(none)"))
        return out

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
    app.on_quit = tui.stop  # 'q' exits the loop
    try:
        tui.start()
    finally:
        tui.stop()
