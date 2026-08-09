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

from xdog.agent.tools.tool_filesystem import CONFINE_CTX_KEY
from xdog.flow.models import NodeDef
from xdog.flow.runners import SdkRunner
from xdog.flow.tools import ToolRegistry


class _CapturingAgent:
    """Stands in for `xdog.agent.Agent`, remembering the ctx it was built with."""

    seen: dict[str, Any] = {}

    def __init__(self, stream_fn: Any, **kwargs: Any) -> None:
        _CapturingAgent.seen = dict(kwargs.get("tool_ctx") or {})
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


async def _tool_ctx_for(monkeypatch: Any, **runner_kwargs: Any) -> dict[str, Any]:
    """Run one agent turn and return the tool_ctx the Agent was constructed with."""
    import xdog.agent.agent as agent_module

    monkeypatch.setattr(agent_module, "Agent", _CapturingAgent)
    _CapturingAgent.seen = {}

    runner = SdkRunner(
        stream_fn_factory=lambda _m: object(),  # type: ignore[arg-type,return-value]
        tool_registry=ToolRegistry(),
        web_search_fn_factory=None,
        **runner_kwargs,
    )
    await runner.run(
        NodeDef(id="n"),
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


def test_an_inline_script_cannot_be_confined() -> None:
    """`exec(node.code, namespace)` runs in the executor's own process, so no
    path check is ever consulted. Confining a workflow containing one would be a
    promise nothing keeps."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "s", "type": "script", "code": "def s(ctx):\n    return 1", "outputs": ["out"]}],
        [{"from": "s", "to": "$output", "map": {"out": "r"}}],
    )
    reasons = unconfinable_reasons(wf)

    assert len(reasons) == 1
    assert "unrestricted Python" in reasons[0]


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
    """A child workflow runs in the parent's process too, so an inline script
    buried in one is exactly as unconfinable — and much easier to miss."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "outer", "type": "subflow", "subflow": {
            "name": "child", "provider": "p", "defaults": {"model": "m"},
            "entry": "inner",
            "nodes": [{"id": "inner", "type": "script",
                       "code": "def inner(ctx):\n    return 1", "outputs": ["out"]}],
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
    assert "FLOW_WORKSPACE" in source
    assert "fs_confine_to" in source
    assert not re.search(r'fs_confine_to["\']\s*:\s*\[["\']/', source), (
        "a hard-coded root would mean the workflow granted its own access"
    )

    namespace: dict[str, Any] = {}
    exec(compile(source, "<gen>", "exec"), namespace)  # noqa: S102 - our own output

    roots = namespace["_confinement_roots"]
    workspace = tmp_path / "runtime"

    old = os.environ.pop("FLOW_WORKSPACE", None)
    try:
        assert roots() is None, "unset means unconfined, matching the interpreter"

        os.environ["FLOW_WORKSPACE"] = str(workspace)
        assert roots() == [str(workspace.resolve())]

        os.environ["FLOW_ALLOW_PATHS"] = str(tmp_path / "data")
        assert roots() == [str(workspace.resolve()), str((tmp_path / "data").resolve())]
    finally:
        os.environ.pop("FLOW_ALLOW_PATHS", None)
        if old is None:
            os.environ.pop("FLOW_WORKSPACE", None)
        else:
            os.environ["FLOW_WORKSPACE"] = old
