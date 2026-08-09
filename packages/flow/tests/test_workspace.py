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
