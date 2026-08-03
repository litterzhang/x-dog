"""Dynamic fan-out (G1) — interpreter behaviour and cross-engine parity.

A ``fan_out`` edge runs its worker node once per element of the source array; each
worker output port is aggregated into an index-ordered list stored under the single
worker node id.  A downstream ``fan_in`` edge reads that list as a plain mapping.

From the scheduler's view the fan group is ONE node (one completed id, one trace
frame), so the static-graph property — and ``interpret == compile`` — holds.
See ``docs/fan-out.md``.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile
import types
from typing import Any

import pytest
from ai.types import AssistantMessage, DoneEvent, TextContent
from ai.utils.event_stream import EventStream as AiEventStream
from flow.codegen import generate
from flow.executor import execute
from flow.loader import parse_workflow


def _map_reduce(n: int, cap: int = 0, worker: str = "script") -> dict[str, Any]:
    """A map-reduce workflow with a runtime-sized fan-out of *n* elements.

    ``worker='script'`` uppercases each element (no LLM); ``worker='agent'`` echoes
    the stubbed model text so the same graph exercises the agent path.
    """
    if worker == "script":
        work_node = {
            "id": "work",
            "type": "script",
            "inputs": ["task"],
            "code": "def w(ctx, task):\n    return task.upper()",
            "outputs": ["res"],
        }
    else:
        work_node = {
            "id": "work",
            "type": "agent",
            "prompt": "echo {{task}}",
            "inputs": ["task"],
            "outputs": ["res"],
        }
    return {
        "name": "mr",
        "provider": "copilot",
        "entry": "plan",
        "defaults": {"model": "m"},
        "state": {"n": n},
        "max_concurrency": cap,
        "nodes": [
            {
                "id": "plan",
                "type": "script",
                "inputs": [{"name": "n", "type": "integer"}],
                "code": "def p(ctx, n):\n    return ['t' + str(i) for i in range(n)]",
                "outputs": [{"name": "tasks", "schema": {"type": "array", "items": {"type": "string"}}}],
            },
            work_node,
            {
                "id": "merge",
                "type": "script",
                "inputs": [{"name": "results", "schema": {"type": "array", "items": {"type": "string"}}}],
                "code": "def m(ctx, results):\n    return ','.join(results)",
                "outputs": ["summary"],
            },
        ],
        "edges": [
            {"from": "$in", "to": "plan", "map": {"n": "n"}},
            {"from": "plan", "to": "work", "fan_out": "tasks", "map": {"tasks": "task"}},
            {"from": "work", "to": "merge", "fan_in": "list", "map": {"res": "results"}},
            {"from": "merge", "to": "$output", "map": {"summary": "summary"}},
        ],
    }


def _stub_factory(text: str) -> Any:
    """A stream_fn_factory whose agent turns emit *text* (no real LLM)."""

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            msg = AssistantMessage(content=(TextContent(text=text),))
            stream: AiEventStream[AssistantMessage] = AiEventStream()

            async def _push() -> None:
                await asyncio.sleep(0)
                await stream.send(DoneEvent(stop_reason="stop", message=msg))
                stream.set_result(msg)
                await stream.close()

            asyncio.ensure_future(_push())
            return stream

        return _stream_fn

    return _factory


async def _interp(data: dict[str, Any]) -> dict[str, Any]:
    wf = parse_workflow(data)
    result = await execute(wf, stream_fn_factory=_stub_factory("X"))
    return dict(result.runtime)


# --- interpreter behaviour -------------------------------------------------


async def test_fan_out_three_elements() -> None:
    rt = await _interp(_map_reduce(3))
    assert rt["state"]["work"]["res"] == ["T0", "T1", "T2"]  # index-ordered list
    assert rt["out"]["summary"] == "T0,T1,T2"


async def test_fan_out_single_element_is_a_list() -> None:
    """N=1 must yield a one-element list, never a bare scalar."""
    rt = await _interp(_map_reduce(1))
    assert rt["state"]["work"]["res"] == ["T0"]
    assert rt["out"]["summary"] == "T0"


async def test_fan_out_empty_array_is_empty_list() -> None:
    """N=0 runs the worker zero times; the reduce is []."""
    rt = await _interp(_map_reduce(0))
    assert rt["state"]["work"]["res"] == []
    assert rt["out"]["summary"] == ""


async def test_fan_out_capped_concurrency_no_deadlock() -> None:
    """cap=1 must not self-nest the semaphore (the fan node holds one outer slot)."""
    rt = await _interp(_map_reduce(3, cap=1))
    assert rt["out"]["summary"] == "T0,T1,T2"


async def test_fan_out_single_trace_frame_for_the_group() -> None:
    """The whole fan is ONE scheduler node: one completed id, one work frame."""
    wf = parse_workflow(_map_reduce(3))
    result = await execute(wf, stream_fn_factory=_stub_factory("X"))
    work_frames = [f for f in result.runtime["stack"] if f["node"] == "work"]
    assert len(work_frames) == 1


async def test_fan_out_agent_worker() -> None:
    """The agent path fans out too — each instance echoes the stubbed text."""
    wf = parse_workflow(_map_reduce(3, worker="agent"))
    result = await execute(wf, stream_fn_factory=_stub_factory("PONG"))
    assert result.runtime["state"]["work"]["res"] == ["PONG", "PONG", "PONG"]


# --- cross-engine parity (interpret == compile) ----------------------------


async def _run_generated(src: str) -> dict[str, Any]:
    """Exec a generated module with stubbed provider/stream and return its _RUNTIME."""
    import agent.helpers as _agent_helpers
    import ai as _ai

    def _stub_stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        msg = AssistantMessage(content=(TextContent(text="X"),))
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason="stop", message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    original_sfp = _agent_helpers.stream_fn_from_provider
    original_provider = _ai.provider
    _agent_helpers.stream_fn_from_provider = lambda _p: _stub_stream_fn  # type: ignore[assignment]
    _ai.provider = lambda _name: object()  # type: ignore[assignment]
    mod = types.ModuleType("_gen_fan")
    try:
        exec(compile(src, "<gen_fan>", "exec"), mod.__dict__)  # noqa: S102
        await mod.main()  # type: ignore[attr-defined]
        return dict(mod._RUNTIME)  # type: ignore[attr-defined]
    finally:
        _agent_helpers.stream_fn_from_provider = original_sfp  # type: ignore[assignment]
        _ai.provider = original_provider  # type: ignore[assignment]


@pytest.mark.parametrize("n", [0, 1, 3])
async def test_fan_out_interpret_equals_compile(n: int) -> None:
    """The map-reduce output is identical through execute() and the generated module."""
    data = _map_reduce(n)
    wf = parse_workflow(data)

    interp = await execute(wf, stream_fn_factory=_stub_factory("X"))
    src = generate(wf)
    compile(src, "<gen>", "exec")  # generated module compiles

    gen_runtime = await _run_generated(src)
    assert gen_runtime["state"] == dict(interp.runtime["state"])
    assert gen_runtime["out"] == dict(interp.runtime["out"])


def test_fan_out_generated_module_is_ruff_clean() -> None:
    import subprocess
    import sys

    src = generate(parse_workflow(_map_reduce(3)))
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
        tf.write(src)
        tmp = pathlib.Path(tf.name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--line-length", "120", str(tmp)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ruff failed:\n{result.stdout}\n{result.stderr}"
    finally:
        tmp.unlink(missing_ok=True)
