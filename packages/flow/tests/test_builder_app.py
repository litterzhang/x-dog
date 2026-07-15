"""Contract/acceptance test for the (flow-generated) BuilderApp TUI shell.

This test is the SPEC the code generator must satisfy.  It drives the app by
feeding synthetic KeyEvents — no real terminal — and asserts the underlying
BuilderModel changes as documented.  Only ``BuilderApp.run()`` touches the
terminal and is not exercised here.

Contract for the generated ``flow/builder/app.py``:

- ``build_app(path) -> BuilderApp`` — loads the workflow at *path* if it exists,
  else starts an empty model named after the file stem.
- ``BuilderApp`` is a ``tui.tui.Component`` (has ``render(width) -> list[str]``
  and ``handle_input(KeyEvent) -> bool``).
- ``BuilderApp.model`` exposes the current ``BuilderModel``.
- Key bindings on ``handle_input``:
  - ``a`` -> add an agent node; ``s`` -> add a script node.
  - ``j`` / ``down`` and ``k`` / ``up`` -> move node selection.
  - ``d`` -> remove the selected node.
  - ``w`` -> save the current workflow to the app's path via
    ``flow.builder.serialize.dump_workflow`` (only when valid); the model then
    becomes non-dirty.
  - unknown keys -> return ``False`` (not consumed).
- ``build_app(path)`` retains *path* so ``w`` knows where to write.
- ``render(width)`` returns a non-empty list of strings that includes each node
  id and a validation status line.
"""

from __future__ import annotations

import pathlib

from flow.builder.app import BuilderApp, build_app
from flow.loader import load_workflow
from tui.keys import KeyEvent
from tui.tui import Component


def _new_app(tmp_path: pathlib.Path) -> BuilderApp:
    return build_app(tmp_path / "wf.json")


def test_build_app_returns_component(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    assert isinstance(app, Component)
    assert app.model.wf.nodes == ()


def test_press_a_adds_agent_node(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    consumed = app.handle_input(KeyEvent(key="a"))
    assert consumed is True
    assert app.model.node_ids == ("agent",)
    assert app.model.wf.nodes[0].type == "agent"


def test_press_s_adds_script_node(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="s"))
    assert app.model.wf.nodes[0].type == "script"


def test_selection_moves_with_j_k(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))
    app.handle_input(KeyEvent(key="a"))
    app.handle_input(KeyEvent(key="a"))
    # selection starts on last-added; move up then assert it changed
    top = app.model.node_ids[0]
    app.handle_input(KeyEvent(key="k"))
    app.handle_input(KeyEvent(key="k"))
    assert app.model.selected == top


def test_press_d_removes_selected(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))
    app.handle_input(KeyEvent(key="d"))
    assert app.model.node_ids == ()


def test_press_w_saves_workflow_json(tmp_path: pathlib.Path) -> None:
    """'w' writes the current workflow to the app's path as reloadable JSON."""
    path = tmp_path / "wf.json"
    app = build_app(path)
    app.handle_input(KeyEvent(key="a"))  # one agent node -> valid
    app.handle_input(KeyEvent(key="w"))  # save
    assert path.exists()
    # the saved file reloads to an equal workflow
    reloaded = load_workflow(path)
    assert reloaded == app.model.wf
    # after a save the model is no longer dirty
    assert app.model.dirty is False


def test_unknown_key_not_consumed(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    assert app.handle_input(KeyEvent(key="z")) is False


def test_render_lists_nodes_and_status(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))
    lines = app.render(80)
    assert isinstance(lines, list) and lines
    joined = "\n".join(lines)
    assert "agent" in joined
