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
  - ``q`` -> quit: invoke the ``on_quit`` callback if one is set (``run()`` wires
    it to ``tui.stop()``); returns ``True``.
  - unknown keys in normal mode -> return ``False`` (not consumed).
- ``BuilderApp`` exposes a settable ``on_quit: Callable[[], None] | None``
  attribute (default ``None``); pressing ``q`` calls it when set.
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
  id and a validation status line, WITHOUT duplicating the node list (the header
  and status line each appear at most once).
"""

from __future__ import annotations

import pathlib

from flow.builder.app import BuilderApp, build_app
from flow.loader import load_workflow
from tui.keys import KeyEvent
from tui.tui import Component
from tui.utils import strip_ansi, visible_width


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
    app.handle_input(KeyEvent(key="tab"))  # graph -> nodes (arrows act on nodes)
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
    joined = strip_ansi("\n".join(lines))
    assert "agent" in joined


def test_builder_saves_and_reloads_svg(tmp_path: pathlib.Path) -> None:
    """Saving to a .svg writes an editable SVG; reopening it restores the model."""
    path = tmp_path / "wf.svg"
    app = build_app(path)
    app.handle_input(KeyEvent(key="a"))  # one agent node -> valid
    app.handle_input(KeyEvent(key="w"))  # save as .svg (embeds JSON)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "<svg" in text and "flow-workflow" in text  # a real SVG carrying the source
    # reopening the same .svg restores an equal workflow
    reopened = build_app(path)
    assert reopened.model.wf == app.model.wf
    # and it can keep being edited + re-saved
    reopened.handle_input(KeyEvent(key="a"))  # add another agent node (stays valid)
    reopened.handle_input(KeyEvent(key="w"))
    again = build_app(path)
    assert again.model.node_ids == ("agent", "agent2")


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
    app.handle_input(KeyEvent(key="tab"))  # graph -> nodes (arrows act on nodes)
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


# --- quit + real-UI contract (added after the flat-render/no-quit bug) --------


def test_q_invokes_on_quit_callback(tmp_path: pathlib.Path) -> None:
    """Pressing 'q' calls the on_quit callback (run() wires it to tui.stop)."""
    app = _new_app(tmp_path)
    called = []
    app.on_quit = lambda: called.append(True)
    consumed = app.handle_input(KeyEvent(key="q"))
    assert consumed is True
    assert called == [True]


def test_on_quit_defaults_to_none_and_q_is_safe(tmp_path: pathlib.Path) -> None:
    """With no on_quit set, 'q' must not raise (just consumed)."""
    app = _new_app(tmp_path)
    assert app.on_quit is None
    assert app.handle_input(KeyEvent(key="q")) is True


def test_render_never_emits_empty_lines_at_zero_width(tmp_path: pathlib.Path) -> None:
    """width<=0 must not collapse the whole UI to empty strings (the pty bug)."""
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))
    for w in (0, -5):
        lines = app.render(w)
        assert isinstance(lines, list) and lines
        # not every line is empty — there is real, visible content
        assert any(line.strip() for line in lines)


def test_render_has_visible_content_and_status(tmp_path: pathlib.Path) -> None:
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))
    joined = strip_ansi("\n".join(app.render(100)))
    assert "agent" in joined          # the node shows up
    assert "valid" in joined.lower()  # a status/validation line is present


def test_render_is_two_column_and_equal_width() -> None:
    """Body rows are two columns joined by a vertical separator, all equal width."""
    import pathlib as _pl

    app = build_app(_pl.Path("packages/flow/examples/research_write_review.json"))
    lines = app.render(100)
    # every rendered line has the same DISPLAY width (columns align under color)
    widths = {visible_width(line) for line in lines}
    assert widths == {100}, f"unequal widths: {sorted(widths)}"
    # a vertical column separator appears in the body
    joined = strip_ansi("\n".join(lines))
    assert "│" in joined
    # left column has a node id, right column has a graph box, on body rows
    assert any("research" in strip_ansi(line) and "┌" in strip_ansi(line) for line in lines) or (
        "research" in joined and "┌" in joined
    )


def test_render_shows_selected_node_details(tmp_path: pathlib.Path) -> None:
    """The DETAILS section shows the selected node's fields."""
    app = build_app(tmp_path / "wf.json")
    app.handle_input(KeyEvent(key="a"))  # agent selected
    app.handle_input(KeyEvent(key="p"))  # edit prompt
    _type(app, "hello world")
    app.handle_input(KeyEvent(key="enter"))
    app.handle_input(KeyEvent(key="tab"))  # graph -> nodes: right panel shows DETAILS
    joined = strip_ansi("\n".join(app.render(100)))
    assert "DETAILS" in joined
    assert "hello world" in joined  # the prompt is visible in details


# --- three-block layout: Tab-cycled focus (Graph / Nodes / Edges) -------------

_RWR = "packages/flow/examples/research_write_review.json"


def _rwr_app() -> BuilderApp:
    return build_app(pathlib.Path(_RWR))


def test_left_panel_has_three_boxed_blocks() -> None:
    """The left panel always shows Graph, Nodes, and Edges as boxed blocks."""
    joined = strip_ansi("\n".join(_rwr_app().render(100)))
    assert "Graph" in joined and "Nodes" in joined and "Edges" in joined
    assert "┌" in joined and "└" in joined  # boxes are drawn


def test_tab_cycles_focus_nodes_edges_graph() -> None:
    """Tab advances focus graph -> nodes -> edges -> graph (wrapping)."""
    app = _rwr_app()
    assert app._focus == "graph"  # default focus is the Graph block
    app.handle_input(KeyEvent(key="tab"))
    assert app._focus == "nodes"
    app.handle_input(KeyEvent(key="tab"))
    assert app._focus == "edges"
    app.handle_input(KeyEvent(key="tab"))
    assert app._focus == "graph"  # wrapped


def test_default_focus_is_graph_and_arrows_are_inert() -> None:
    """The builder opens on the Graph block; up/down do NOT move the node selection."""
    app = _rwr_app()
    assert app._focus == "graph"
    before = app.model.selected
    app.handle_input(KeyEvent(key="down"))
    app.handle_input(KeyEvent(key="j"))
    app.handle_input(KeyEvent(key="up"))
    assert app.model.selected == before  # graph focus swallows navigation


def test_graph_focus_shows_diagram_on_right() -> None:
    """With the Graph block focused (the default), the right panel shows the diagram."""
    app = _rwr_app()
    joined = strip_ansi("\n".join(app.render(100)))
    assert "GRAPH" in joined
    # the boxed node diagram from to_ascii_diagram is present
    assert "[agent]" in joined


def test_edges_focus_shows_edge_detail_and_param_flow() -> None:
    """With the Edges block focused, the right panel shows 'src -> dst' + param flow."""
    app = _rwr_app()
    app.handle_input(KeyEvent(key="tab"))  # -> nodes
    app.handle_input(KeyEvent(key="tab"))  # -> edges
    joined = strip_ansi("\n".join(app.render(100)))
    assert "EDGE" in joined
    assert "research → write" in joined  # edge expressed as node -> node
    assert "parameter flow" in joined  # produces/consumes wiring shown


def test_jk_navigate_edges_when_edges_focused() -> None:
    """In the Edges block, j/k move the edge selection (not the node selection)."""
    app = _rwr_app()
    app.handle_input(KeyEvent(key="tab"))  # -> nodes
    app.handle_input(KeyEvent(key="tab"))  # -> edges
    selected_before = app.model.selected
    assert app._edge_idx == 0
    app.handle_input(KeyEvent(key="j"))
    assert app._edge_idx == 1
    app.handle_input(KeyEvent(key="k"))
    assert app._edge_idx == 0
    # node selection is untouched while navigating edges
    assert app.model.selected == selected_before


def test_d_deletes_edge_when_edges_focused() -> None:
    """In the Edges block, 'd' removes the selected edge, leaving nodes intact."""
    app = _rwr_app()
    app.handle_input(KeyEvent(key="tab"))  # -> nodes
    app.handle_input(KeyEvent(key="tab"))  # -> edges
    edges_before = len(app.model.wf.edges)
    nodes_before = len(app.model.wf.nodes)
    app.handle_input(KeyEvent(key="d"))
    assert len(app.model.wf.edges) == edges_before - 1
    assert len(app.model.wf.nodes) == nodes_before  # nodes untouched


def test_equal_width_across_all_foci() -> None:
    """Every focus renders equal-width rows (columns stay aligned under color)."""
    app = _rwr_app()
    for _ in range(3):
        widths = {visible_width(line) for line in app.render(100)}
        assert widths == {100}, f"unequal widths at focus {app._focus}: {sorted(widths)}"
        app.handle_input(KeyEvent(key="tab"))


def test_multiline_field_does_not_corrupt_layout(tmp_path: pathlib.Path) -> None:
    """A node whose prompt contains newlines must not break the single-line rows.

    Regression: selecting a node with a multiline prompt spilled the newline into
    a rendered row, so one element spanned multiple physical lines — the moving
    selection then corrupted the box layout (duplicated/broken frames).
    """
    app = build_app(tmp_path / "wf.json")
    app.handle_input(KeyEvent(key="a"))  # agent selected
    app.handle_input(KeyEvent(key="p"))  # edit prompt
    _type(app, "line one")
    # inject a real newline (as a loaded workflow would carry) via the action layer
    app.handle_input(KeyEvent(key="enter"))
    from flow.builder import actions

    app._model = actions.set_field(app.model, "agent", "prompt", "line one\n\nline two")
    app.handle_input(KeyEvent(key="tab"))  # graph -> nodes: DETAILS shows the prompt
    lines = app.render(100)
    # no rendered element carries an embedded newline/tab (each row is one line)
    assert all("\n" not in line and "\t" not in line for line in lines)
    # joining and splitting yields the SAME number of physical lines (no spill)
    assert len("\n".join(lines).split("\n")) == len(lines)
    # both prompt fragments are still visible (flattened onto one row)
    joined = strip_ansi("\n".join(lines))
    assert "line one" in joined and "line two" in joined


# --- Shift+Tab page navigation: Builder / Functions / Tools -------------------

_CGB = "packages/flow/examples/codegen_builder.json"  # has code + run: scripts, tools


def _cgb_app() -> BuilderApp:
    return build_app(pathlib.Path(_CGB))


def _shift_tab(app: BuilderApp) -> None:
    app.handle_input(KeyEvent(key="tab", shift=True))


def test_shift_tab_cycles_pages() -> None:
    """Shift+Tab advances the top-level page builder -> functions -> tools -> builder."""
    app = _cgb_app()
    assert app._page == "builder"
    _shift_tab(app)
    assert app._page == "functions"
    _shift_tab(app)
    assert app._page == "tools"
    _shift_tab(app)
    assert app._page == "builder"  # wrapped


def test_functions_page_lists_scripts_and_shows_source() -> None:
    """The Functions page lists the workflow's script nodes and shows their source."""
    app = _cgb_app()
    _shift_tab(app)  # -> functions
    joined = strip_ansi("\n".join(app.render(120)))
    assert "Functions" in joined
    assert "SOURCE" in joined
    # codegen_builder has script nodes 'intake' (run:) and 'verify' (run:)
    assert "intake" in joined
    # the right pane shows real imported source (a def/async def line)
    assert "def " in joined


def test_functions_page_navigates_scripts() -> None:
    """j/k move the selected script on the Functions page."""
    app = _cgb_app()
    _shift_tab(app)  # -> functions
    assert app._fn_idx == 0
    app.handle_input(KeyEvent(key="j"))
    assert app._fn_idx == 1
    app.handle_input(KeyEvent(key="k"))
    assert app._fn_idx == 0


def test_tools_page_lists_builtin_and_shows_source() -> None:
    """The Tools page lists built-in tools with description + execute source."""
    app = _cgb_app()
    _shift_tab(app)  # -> functions
    _shift_tab(app)  # -> tools
    joined = strip_ansi("\n".join(app.render(120)))
    assert "Tools" in joined
    assert "builtin" in joined  # origin tag
    assert "bash" in joined and "filesystem" in joined
    assert "source" in joined.lower()  # the source divider/section


def test_tools_page_shows_custom_tool_from_manifest(tmp_path: pathlib.Path) -> None:
    """A workflow with a tool manifest surfaces its custom tool on the Tools page."""
    import shutil

    # Bundle a fixture tool module beside a manifest workflow.
    fixtures = pathlib.Path(__file__).parent / "fixtures"
    shutil.copy(fixtures / "mytools.py", tmp_path / "mytools.py")
    wf_json = tmp_path / "wf.json"
    wf_json.write_text(
        """{
          "name": "tw", "provider": "copilot", "defaults": {"model": "m"}, "entry": "a",
          "tools": {"reverse": "mytools:make_reverse"},
          "nodes": [{"id": "a", "type": "agent", "model": "m", "prompt": "p", "tools": ["reverse"]}],
          "edges": []
        }""",
        encoding="utf-8",
    )
    app = build_app(wf_json)
    _shift_tab(app)  # -> functions
    _shift_tab(app)  # -> tools
    # Move selection onto the custom tool (last in the list, after builtins).
    infos = app._tool_infos()
    app._tool_idx = next(i for i, info in enumerate(infos) if info.name == "reverse")
    joined = strip_ansi("\n".join(app.render(120)))
    assert "reverse" in joined
    assert "custom" in joined  # origin tag
    assert "Reverse the given text." in joined  # description


def test_shift_tab_ignored_in_prompt_mode(tmp_path: pathlib.Path) -> None:
    """Shift+Tab must not switch pages while editing a prompt (text mode owns keys)."""
    app = _new_app(tmp_path)
    app.handle_input(KeyEvent(key="a"))
    app.handle_input(KeyEvent(key="p"))  # enter prompt mode
    _shift_tab(app)
    assert app._page == "builder"  # unchanged


# --- Functions page: run: source resolution + dedent -------------------------


def test_functions_page_reads_run_source_from_subdir(tmp_path: pathlib.Path) -> None:
    """A run: module living in scripts/ is shown even though it isn't importable."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "nodes.py").write_text(
        "import a_missing_dependency_xyz\n\n\ndef scope(ctx, repo):\n    return '{}'\n",
        encoding="utf-8",
    )
    wf_json = tmp_path / "wf.json"
    wf_json.write_text(
        """{
          "name": "sub", "provider": "copilot", "defaults": {"model": "m"}, "entry": "scope",
          "nodes": [{"id": "scope", "type": "script", "run": "nodes:scope",
                     "inputs": ["repo"], "output": "scope"}],
          "edges": [{"from": "$in", "to": "scope", "map": {"repo": "repo"}}],
          "state": {"repo": "/x"}
        }""",
        encoding="utf-8",
    )
    app = build_app(wf_json)
    _shift_tab(app)  # -> functions
    joined = strip_ansi("\n".join(app.render(90)))
    assert "def scope(ctx, repo)" in joined  # source located statically
    assert "source unavailable" not in joined


def test_tools_source_is_dedented() -> None:
    """A tool's execute source (a class method) is dedented to column 0.

    ``inspect.getsource`` on a method keeps its class-body indentation; the
    viewer must strip it so the ``async def`` sits flush against the box border
    (rendered as ``│async def execute(``), not indented four spaces.
    """
    app = _cgb_app()
    _shift_tab(app)  # functions
    _shift_tab(app)  # tools — first tool (bash) is a builtin with method source
    rows = [strip_ansi(line) for line in app.render(120)]
    # box padding is trailing-only, so a dedented line renders as '│async def…'.
    assert any("│async def execute(" in r for r in rows)
    # and NOT the original 4-space-indented form
    assert not any("│    async def execute(" in r for r in rows)


# --- fill-screen + scroll ----------------------------------------------------


def test_render_fills_screen_height(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The rendered frame spans the full terminal height, not just the content."""
    app = _cgb_app()
    monkeypatch.setattr(BuilderApp, "_screen_height", staticmethod(lambda: 40))
    _shift_tab(app)  # functions (short left list, long right source)
    assert len(app.render(100)) == 40


def test_tools_page_scrolls_right_pane(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """PageDown reveals lower source lines; g returns to the top."""
    app = _cgb_app()
    monkeypatch.setattr(BuilderApp, "_screen_height", staticmethod(lambda: 20))
    _shift_tab(app)  # functions
    _shift_tab(app)  # tools
    top_view = strip_ansi("\n".join(app.render(110)))
    app.handle_input(KeyEvent(key="pagedown"))
    assert app._scroll > 0
    scrolled_view = strip_ansi("\n".join(app.render(110)))
    assert scrolled_view != top_view  # the source pane moved
    app.handle_input(KeyEvent(key="g"))
    assert app._scroll == 0


def test_changing_selection_resets_scroll(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Moving to a different tool/script resets the source scroll to the top."""
    app = _cgb_app()
    monkeypatch.setattr(BuilderApp, "_screen_height", staticmethod(lambda: 20))
    _shift_tab(app)  # functions
    _shift_tab(app)  # tools
    app.handle_input(KeyEvent(key="pagedown"))
    assert app._scroll > 0
    app.handle_input(KeyEvent(key="down"))  # select next tool
    assert app._scroll == 0


def test_shift_tab_resets_scroll(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Switching pages starts the new page scrolled to the top."""
    app = _cgb_app()
    monkeypatch.setattr(BuilderApp, "_screen_height", staticmethod(lambda: 20))
    _shift_tab(app)  # functions
    _shift_tab(app)  # tools
    app.handle_input(KeyEvent(key="pagedown"))
    assert app._scroll > 0
    _shift_tab(app)  # -> builder
    assert app._scroll == 0
