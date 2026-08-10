"""Workspace confinement, end to end through the executor.

The unit tests in `xdog-agent` cover the allowlist itself. These cover the thing
that is easy to get wrong and impossible to notice: a run that computes a
workspace perfectly and then never passes it to the tool would satisfy every
"it did not crash" test. So each of these asserts on what the tool was actually
handed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from xdog.agent.tools.tool_filesystem import CONFINE_CTX_KEY, WORKSPACE_CTX_KEY
from xdog.flow.models import NodeDef
from xdog.flow.runners import SdkRunner
from xdog.flow.tools import ToolRegistry


class _CapturingAgent:
    """Stands in for `xdog.agent.Agent`, remembering the ctx it was built with."""

    seen: dict[str, Any] = {}

    prompt_seen: str = ""

    def __init__(self, stream_fn: Any, **kwargs: Any) -> None:
        _CapturingAgent.seen = dict(kwargs.get("tool_ctx") or {})
        config = kwargs.get("config")
        _CapturingAgent.prompt_seen = str(getattr(config, "system_prompt", "") or "")
        self._kwargs = kwargs

    @property
    def state(self) -> Any:
        raise AssertionError("not reached")

    def dump(self) -> dict[str, Any]:
        return {}

    async def prompt(self, text: str) -> Any:
        class _Empty:
            def __aiter__(self) -> Any:
                return self

            async def __anext__(self) -> Any:
                raise StopAsyncIteration

        return _Empty()


async def _tool_ctx_for(
    monkeypatch: Any, *, node_tools: tuple[str, ...] = (), **runner_kwargs: Any
) -> dict[str, Any]:
    """Run one agent turn and return the tool_ctx the Agent was constructed with."""
    import xdog.agent.agent as agent_module
    from xdog.flow.tools import default_registry

    monkeypatch.setattr(agent_module, "Agent", _CapturingAgent)
    _CapturingAgent.seen = {}
    _CapturingAgent.prompt_seen = ""

    runner = SdkRunner(
        stream_fn_factory=lambda _m: object(),  # type: ignore[arg-type,return-value]
        tool_registry=default_registry() if node_tools else ToolRegistry(),
        web_search_fn_factory=None,
        **runner_kwargs,
    )
    await runner.run(
        NodeDef(id="n", tools=node_tools),
        system_prompt="",
        user_prompt="go",
        model="m",
        timeout=5.0,
        inputs={},
        step=0,
        fan_index=None,
    )
    return _CapturingAgent.seen


async def test_an_unconfined_run_passes_no_bound(monkeypatch: Any) -> None:
    """The default, and what every caller before this got. `xdog-coding` and
    `xdog-claw` build their own agents and must be unaffected."""
    ctx = await _tool_ctx_for(monkeypatch)

    assert CONFINE_CTX_KEY not in ctx


async def test_a_workspace_reaches_the_tool(monkeypatch: Any, tmp_path: Path) -> None:
    """The wiring that the whole feature rests on."""
    workspace = tmp_path / "runtime"

    ctx = await _tool_ctx_for(monkeypatch, confine_to=[workspace])

    assert ctx[CONFINE_CTX_KEY] == [str(workspace)]


async def test_granted_trees_are_added_to_the_workspace(
    monkeypatch: Any, tmp_path: Path
) -> None:
    workspace = tmp_path / "runtime"
    granted = tmp_path / "data"

    ctx = await _tool_ctx_for(monkeypatch, confine_to=[workspace, granted])

    assert ctx[CONFINE_CTX_KEY] == [str(workspace), str(granted)]


async def test_confinement_survives_a_structured_node(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A structured node builds its own `tool_ctx` for `submit_result`. Before
    this was merged rather than assigned, that overwrote the confinement — the
    bound would silently vanish for exactly the nodes doing the most work."""
    from xdog.flow.models import Port

    workspace = tmp_path / "runtime"
    import xdog.agent.agent as agent_module

    monkeypatch.setattr(agent_module, "Agent", _CapturingAgent)
    _CapturingAgent.seen = {}

    runner = SdkRunner(
        stream_fn_factory=lambda _m: object(),  # type: ignore[arg-type,return-value]
        tool_registry=ToolRegistry(),
        web_search_fn_factory=None,
        confine_to=[workspace],
    )
    structured = NodeDef(
        id="n",
        output_ports=(
            Port("a", schema={"type": "string"}),
            Port("b", schema={"type": "string"}),
        ),
    )
    try:
        await runner.run(
            structured,
            system_prompt="",
            user_prompt="go",
            model="m",
            timeout=5.0,
            inputs={},
            step=0,
            fan_index=None,
        )
    except Exception:
        pass  # the stub agent submits nothing; we only care about the ctx

    assert _CapturingAgent.seen[CONFINE_CTX_KEY] == [str(workspace)]
    assert "flow_result_sink" in _CapturingAgent.seen, "and the sink is still there"


# -- What --confined refuses, and why it must -------------------------------


def _wf(nodes: list[dict], edges: list[dict]) -> Any:
    from xdog.flow.loader import parse_workflow

    return parse_workflow({
        "name": "c", "provider": "p", "defaults": {"model": "m"},
        "entry": nodes[0]["id"], "nodes": nodes, "edges": edges,
    })


def test_a_confinable_workflow_has_no_reasons() -> None:
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "a", "type": "agent", "prompt": "go", "tools": ["filesystem"],
          "outputs": ["out"]}],
        [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    )
    assert unconfinable_reasons(wf) == []


def test_an_inline_script_is_no_longer_a_reason_to_refuse() -> None:
    """This used to refuse, and the reasoning was right at the time: `exec` in
    the executor's own process met no check. The audit hook is that check. A
    script node now runs and gets stopped when it reaches for something, which
    is a better answer than refusing the workflow that contains it."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "s", "type": "script", "code": "def s(ctx):\n    return 1", "outputs": ["out"]}],
        [{"from": "s", "to": "$output", "map": {"out": "r"}}],
    )

    assert unconfinable_reasons(wf) == []


def test_what_still_refuses_is_what_leaves_this_process() -> None:
    """The remaining rules are not a list of bad things; they are the boundary of
    what an in-process hook can see."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "a", "type": "agent", "prompt": "go", "tools": ["bash"], "outputs": ["out"]}],
        [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    )
    assert unconfinable_reasons(wf), "a shell is a child process; the hook cannot follow it"


def test_a_run_ref_script_can_be_confined() -> None:
    """`run: module:callable` imports reviewed code from disk rather than
    executing text carried inside the workflow — the same trust as any other
    dependency, so it is not a reason to refuse."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "s", "type": "script", "run": "mymod:fn", "outputs": ["out"]}],
        [{"from": "s", "to": "$output", "map": {"out": "r"}}],
    )
    assert unconfinable_reasons(wf) == []


def test_bash_and_cli_backends_cannot_be_confined() -> None:
    from xdog.flow.loader import unconfinable_reasons

    with_bash = _wf(
        [{"id": "a", "type": "agent", "prompt": "go", "tools": ["bash"], "outputs": ["out"]}],
        [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    )
    assert any("general-purpose escape" in r for r in unconfinable_reasons(with_bash))

    with_cli = _wf(
        [{"id": "a", "type": "agent", "prompt": "go", "backend": "claude-cli",
          "outputs": ["out"]}],
        [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    )
    assert any("owns its own session" in r for r in unconfinable_reasons(with_cli))


def test_a_subflow_hides_nothing() -> None:
    """A child runs in the parent's process, so a `bash` tool buried in one is
    exactly as unconfinable as at the top level — and much easier to miss."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "outer", "type": "subflow", "subflow": {
            "name": "child", "provider": "p", "defaults": {"model": "m"},
            "entry": "inner",
            "nodes": [{"id": "inner", "type": "agent", "prompt": "go",
                       "tools": ["bash"], "outputs": ["out"]}],
            "edges": [{"from": "inner", "to": "$output", "map": {"out": "out"}}],
        }}],
        [{"from": "outer", "to": "$output", "map": {"out": "r"}}],
    )
    reasons = unconfinable_reasons(wf)

    assert reasons and "subflow 'outer'" in reasons[0]


# -- interpret == compile ----------------------------------------------------


def test_the_generated_module_confines_the_same_way(tmp_path: Path) -> None:
    """The invariant, and the one place it matters most.

    Confinement shipped in the interpreter first and codegen knew nothing about
    it, so the same write that `--confined` refused succeeded when the workflow
    was compiled. Nothing failed; the bound simply was not there. A divergence
    in the *permissive* direction is the worst kind, because the run that skips
    the check is the one that looks like it worked.
    """
    import os
    import re

    from xdog.flow.codegen import generate
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "c", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "go", "tools": ["filesystem"],
                   "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    })
    source = generate(wf)

    # The generated module reads its bound from the environment, never from its
    # own source — a module told by its own text what it may touch is not bound.
    assert "FLOW_CONFINED" in source
    assert "fs_confine_to" in source
    assert "fs_workspace" in source
    assert not re.search(r'fs_confine_to["\']\s*:\s*\[["\']/', source), (
        "a hard-coded root would mean the workflow granted its own access"
    )

    namespace: dict[str, Any] = {}
    exec(compile(source, "<gen>", "exec"), namespace)  # noqa: S102 - our own output

    roots = namespace["_confinement_roots"]
    workspace_dir = namespace["_workspace_dir"]
    _granted = namespace["_granted_paths"]
    workspace = tmp_path / "runtime"

    old_ws = os.environ.pop("FLOW_WORKSPACE", None)
    old_conf = os.environ.pop("FLOW_CONFINED", None)
    try:
        # A workspace always exists — that is the half that is not opt-in.
        assert workspace_dir().name == "runtime"
        assert roots() is None, "but nothing is enforced until asked, as in the interpreter"

        os.environ["FLOW_WORKSPACE"] = str(workspace)
        assert workspace_dir() == workspace
        assert roots() is None, "naming a workspace still does not confine to it"

        os.environ["FLOW_CONFINED"] = "1"
        assert roots() == [str(workspace.resolve())]

        os.environ["FLOW_ALLOW_PATHS"] = str(tmp_path / "data")
        assert roots() == [str(workspace.resolve()), str((tmp_path / "data").resolve())]
        # And the briefing: both engines must tell the model the same thing.
        # A generated module that confines correctly but describes the bound
        # differently produces a *different run*, since the prompt is an input.
        # The equality is near-tautological once codegen calls the shared
        # function — which is the point. The assertion that carries weight is
        # that it calls it at all, and with this run's workspace and roots
        # rather than a string of its own.
        from xdog.agent.workspace import workspace_briefing

        assert "workspace_briefing(" in source, "the module briefs rather than staying silent"
        assert "_workspace_dir()" in source and "_roots" in source, (
            "and briefs from the live values, not from anything baked into its text"
        )
        assert workspace_briefing(workspace_dir(), _granted(), confined=True) == (
            workspace_briefing(workspace, [tmp_path / "data"], confined=True)
        )
    finally:
        os.environ.pop("FLOW_ALLOW_PATHS", None)
        for key, old in (("FLOW_WORKSPACE", old_ws), ("FLOW_CONFINED", old_conf)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


# -- the grant in the scheduling unit ----------------------------------------


def _scheduled(node: dict) -> Any:
    from xdog.flow.loader import parse_workflow

    return parse_workflow({
        "name": "sched", "provider": "p", "defaults": {"model": "m"}, "entry": node["id"],
        "schedule": {"mode": "timer", "every": "1h"},
        "nodes": [node],
        "edges": [{"from": node["id"], "to": "$output", "map": {"out": "r"}}],
    })


def test_the_timer_unit_carries_the_grant(tmp_path: Path) -> None:
    """A scheduled run is the case confinement was written for: nobody is
    watching. The unit is where the grant belongs, because the person who wrote
    the unit is the one with authority to give it."""
    from xdog.flow.scheduler.systemd import ConfineGrant, render_timer_units

    wf = _scheduled({"id": "a", "type": "agent", "prompt": "go", "tools": ["filesystem"],
                     "outputs": ["out"]})
    grant = ConfineGrant(workspace=tmp_path / "runtime", allow_paths=(tmp_path / "data",))

    units = render_timer_units("sched", tmp_path / "bundle", wf.schedule, confine=grant)  # type: ignore[arg-type]
    service = units.files["sched.service"]

    assert f"Environment=FLOW_WORKSPACE={tmp_path / 'runtime'}" in service
    assert f"Environment=FLOW_ALLOW_PATHS={tmp_path / 'data'}" in service


def test_an_unconfined_install_renders_exactly_what_it_did_before(tmp_path: Path) -> None:
    """The default path must not change: every existing install is unconfined."""
    from xdog.flow.scheduler.systemd import render_timer_units

    wf = _scheduled({"id": "a", "type": "agent", "prompt": "go", "outputs": ["out"]})

    service = render_timer_units("sched", tmp_path / "b", wf.schedule).files["sched.service"]  # type: ignore[arg-type]

    assert "FLOW_WORKSPACE" not in service


def test_installing_an_unconfinable_workflow_confined_is_refused(tmp_path: Path) -> None:
    """At install time, when someone is present to read why — rather than at 3am
    with only a systemd status to explain it."""
    import pytest
    from xdog.flow.scheduler.install import Installer
    from xdog.flow.scheduler.systemd import ConfineGrant

    wf = _scheduled({"id": "s", "type": "agent", "prompt": "go", "tools": ["bash"],
                     "outputs": ["out"]})
    installer = Installer(unit_dir=tmp_path / "units", data_dir=tmp_path / "data")

    with pytest.raises(ValueError, match="cannot be confined"):
        installer.install(wf, dry_run=True, confine=ConfineGrant(workspace=tmp_path / "ws"))


def test_a_hook_workflow_gets_its_grant_through_the_registry(tmp_path: Path) -> None:
    """A hook workflow has no unit of its own — the shared listener spawns it —
    so the registry is the only place the grant can live. If the listener did not
    apply it, `--confined` would be silently inert for exactly the mode that runs
    on someone else's schedule."""
    from xdog.flow.scheduler.listener import HookRoute, Router

    fired: list[dict[str, str]] = []
    route = HookRoute(
        name="h", bundle=tmp_path / "b", signal="go",
        listen={"type": "http", "path": "/h"},
        confine={"FLOW_WORKSPACE": str(tmp_path / "ws")},
    )
    router = Router(routes=[route], spawn=lambda _b, env: fired.append(env))

    router.deliver_http("/h", b'{"x": 1}')

    assert fired[0]["FLOW_WORKSPACE"] == str(tmp_path / "ws")


# -- the workspace is on by default; confinement is not ----------------------


async def _prompt_for(monkeypatch: Any, **runner_kwargs: Any) -> str:
    await _tool_ctx_for(monkeypatch, **runner_kwargs)
    return _CapturingAgent.prompt_seen


async def test_a_default_run_gets_a_workspace_but_no_bound(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """The two halves, and why they are separate. Every run has a workspace so
    relative paths land somewhere predictable; only `--confined` makes leaving it
    an error. Collapsing these — as the first implementation did — means an
    ordinary workflow has no workspace at all until someone asks to be confined."""
    workspace = tmp_path / "runtime"

    ctx = await _tool_ctx_for(monkeypatch, workspace=workspace)

    assert ctx[WORKSPACE_CTX_KEY] == str(workspace)
    assert CONFINE_CTX_KEY not in ctx, "a workspace is a convention until confined"


async def test_the_agent_is_told_where_its_workspace_is(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A bound the model cannot see is one it can only find by tripping over it,
    which costs a turn and looks to the model like a malfunction."""
    workspace = tmp_path / "runtime"

    prompt = await _prompt_for(
        monkeypatch, node_tools=("filesystem",), workspace=workspace
    )

    assert str(workspace) in prompt
    assert "Do not read or write anything outside" in prompt, (
        "the instruction is the point: unconfined, this is all we have"
    )
    assert "This run is confined" not in prompt, "nothing is actually refused yet"


async def test_a_confined_agent_is_told_the_walls_and_the_grants(
    monkeypatch: Any, tmp_path: Path
) -> None:
    workspace = tmp_path / "runtime"
    granted = tmp_path / "data"

    prompt = await _prompt_for(
        monkeypatch, node_tools=("filesystem",), workspace=workspace,
        confine_to=[workspace, granted],
    )

    assert str(workspace) in prompt
    assert str(granted) in prompt, "a grant it is not told about is a grant it cannot use"
    assert "This run is confined" in prompt


async def test_even_a_node_with_no_declared_tools_is_briefed(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """This started out keyed off the tool list and that was a guess.

    A node's declared tools do not bound what it can touch: a custom tool can
    write files, so can an MCP server, and a model can name a path for a
    downstream node to use. The workspace is where this run's files belong,
    which is true of a node whether or not we can see how it writes them.
    """
    prompt = await _prompt_for(monkeypatch, workspace=tmp_path / "runtime")

    assert str(tmp_path / "runtime") in prompt


def test_the_two_engines_default_their_workspace_to_different_places(tmp_path: Path) -> None:
    """The one place `interpret == compile` deliberately does not mean "the same
    path", and it is worth pinning so it stays deliberate.

    Both engines apply the same rule — `runtime/` beside the artifact — but the
    artifact differs: the interpreter's is the workflow file, the compiled one's
    is the module. Making the module use the workflow file's directory would be
    worse than the difference, since a bundle routinely runs on a machine where
    that file does not exist. Anyone who needs them to agree names the workspace
    explicitly, which is what `--workspace` and `FLOW_WORKSPACE` are for.
    """
    import os

    from xdog.flow.codegen import generate
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "c", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "go", "tools": ["filesystem"],
                   "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    })
    namespace: dict[str, Any] = {}
    old = os.environ.pop("FLOW_WORKSPACE", None)
    try:
        exec(compile(generate(wf), "<gen>", "exec"), namespace)  # noqa: S102 - our own output

        # Same rule, different anchor.
        assert namespace["_workspace_dir"]().name == "runtime"
        assert namespace["_workspace_dir"]() != tmp_path / "runtime"

        # And naming it explicitly makes them agree, which is the escape hatch.
        os.environ["FLOW_WORKSPACE"] = str(tmp_path / "runtime")
        assert namespace["_workspace_dir"]() == tmp_path / "runtime"
    finally:
        if old is None:
            os.environ.pop("FLOW_WORKSPACE", None)
        else:
            os.environ["FLOW_WORKSPACE"] = old


def test_the_generated_guard_enforces_the_same_events() -> None:
    """The generated module carries its own copy of the audit guard, because a
    script-only bundle installs no xdog-agent. Two copies of a security-relevant
    table is exactly the drift that let codegen ship unconfined once already, so
    the tables are compared rather than trusted.
    """
    from xdog.agent import workspace as lib
    from xdog.flow.codegen import generate
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "s", "provider": "p", "defaults": {"model": "m"}, "entry": "s",
        "nodes": [{"id": "s", "type": "script", "outputs": ["out"],
                   "code": "def s(ctx):\n    return 1"}],
        "edges": [{"from": "s", "to": "$output", "map": {"out": "r"}}],
    })
    namespace: dict[str, Any] = {}
    exec(compile(generate(wf), "<gen>", "exec"), namespace)  # noqa: S102 - our own output

    assert namespace["_WRITE_EVENTS"] == lib._WRITE_EVENTS, (
        "an event enforced in one engine and not the other is a bound that "
        "depends on how the workflow was run"
    )
    assert namespace["_UNAUDITABLE"] == lib._UNAUDITABLE


def test_a_script_node_is_told_where_its_workspace_is(tmp_path: Path) -> None:
    """Being refused for writing outside a directory you were never told about
    is a trap, not a rule — and relative paths are no help, since nodes run
    concurrently and the executor cannot chdir on a script's behalf."""
    import asyncio

    from xdog.flow.executor import execute
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "s", "provider": "p", "defaults": {"model": "m"}, "entry": "s",
        "nodes": [{"id": "s", "type": "script", "outputs": ["ws_path"], "code":
                   "def s(ctx):\n"
                   "    (ctx.workspace / 'out.txt').write_text('written')\n"
                   "    return str(ctx.workspace)"}],
        "edges": [{"from": "s", "to": "$output", "map": {"ws_path": "r"}}],
    })
    ws = tmp_path / "runtime"

    result = asyncio.run(execute(wf, workspace=ws, timeout=10))

    assert result.runtime["out"]["r"] == str(ws)
    assert (ws / "out.txt").read_text() == "written", "and writing there is allowed"
