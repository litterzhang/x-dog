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
- Key bindings on ``handle_input`` (in normal mode):
  - ``a`` -> add an agent node; ``s`` -> add a script node.
  - ``j`` / ``down`` and ``k`` / ``up`` -> move node selection.
  - ``d`` -> remove the selected node.
  - ``w`` -> save the current workflow to the app's path via
    ``flow.builder.serialize.dump_workflow`` (only when valid); the model then
    becomes non-dirty.
  - ``p`` -> enter PROMPT-EDIT mode for the selected node (see below).
  - ``e`` -> enter EDGE mode, remembering the selected node as the edge source.
  - unknown keys in normal mode -> return ``False`` (not consumed).
- PROMPT-EDIT mode (entered with ``p`` when a node is selected):
  - printable single-character keys append to a text buffer seeded from the
    node's current ``prompt``; ``backspace`` deletes the last char.
  - ``enter`` commits the buffer via ``actions.set_field(model, node, "prompt", buf)``
    and returns to normal mode.
  - ``escape`` cancels (no change) and returns to normal mode.
  - while in prompt-edit mode, ``handle_input`` returns ``True`` for these keys.
- EDGE mode (entered with ``e`` when a node is selected):
  - ``j``/``k`` (or arrows) move the selection to choose the destination node.
  - ``enter`` adds an edge source->selected via ``actions.add_edge`` and returns
    to normal mode; ``escape`` cancels.
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


# --- prompt-edit mode ---------------------------------------------------------


def _type(app: BuilderApp, text: str) -> None:
    for ch in text:
        app.handle_input(KeyEvent(key=ch))


def test_prompt_edit_commits_on_enter(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))  # 'agent' selected
    consumed = app.handle_input(KeyEvent(key="p"))  # enter prompt-edit mode
    assert consumed is True
    _type(app, "hello")
    # while editing, printable keys are consumed (not treated as add-node etc.)
    app.handle_input(KeyEvent(key="enter"))
    assert app.model.wf.nodes[0].prompt == "hello"


def test_prompt_edit_backspace(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))
    app.handle_input(KeyEvent(key="p"))
    _type(app, "abc")
    app.handle_input(KeyEvent(key="backspace"))
    app.handle_input(KeyEvent(key="enter"))
    assert app.model.wf.nodes[0].prompt == "ab"


def test_prompt_edit_escape_cancels(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))
    app.handle_input(KeyEvent(key="p"))
    _type(app, "discarded")
    app.handle_input(KeyEvent(key="escape"))
    assert app.model.wf.nodes[0].prompt == ""
    # back in normal mode: 'a' adds a node again
    app.handle_input(KeyEvent(key="a"))
    assert len(app.model.wf.nodes) == 2


def test_prompt_edit_does_not_add_nodes_while_typing(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))  # one node
    app.handle_input(KeyEvent(key="p"))
    _type(app, "a s d")  # these are text, not add/delete commands
    app.handle_input(KeyEvent(key="enter"))
    assert len(app.model.wf.nodes) == 1
    assert app.model.wf.nodes[0].prompt == "a s d"


# --- edge mode ----------------------------------------------------------------


def test_edge_mode_connects_source_to_dest(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))  # 'agent'  (selected)
    app.handle_input(KeyEvent(key="a"))  # 'agent2' (selected)
    # select the source
    app.handle_input(KeyEvent(key="k"))  # move selection up to 'agent'
    assert app.model.selected == "agent"
    app.handle_input(KeyEvent(key="e"))  # edge mode, source = 'agent'
    app.handle_input(KeyEvent(key="j"))  # move dest selection to 'agent2'
    app.handle_input(KeyEvent(key="enter"))  # commit edge agent -> agent2
    assert any(edge.src == "agent" and edge.dst == "agent2" for edge in app.model.wf.edges)


def test_edge_mode_escape_cancels(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))
    app.handle_input(KeyEvent(key="a"))
    app.handle_input(KeyEvent(key="e"))
    app.handle_input(KeyEvent(key="escape"))
    assert app.model.wf.edges == ()
    # normal mode restored
    app.handle_input(KeyEvent(key="a"))
    assert len(app.model.wf.nodes) == 3
