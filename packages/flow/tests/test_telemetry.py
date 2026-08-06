"""Tests for flow.telemetry — MetricsCollector."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, Usage
from xdog.ai.utils.event_stream import EventStream as AiEventStream
from xdog.flow.events import NodeFailed, NodeFinished, NodeStarted
from xdog.flow.executor import execute
from xdog.flow.models import EdgeDef, NodeDef, Port, WorkflowDef
from xdog.flow.telemetry import MetricsCollector

# ---------------------------------------------------------------------------
# helpers (mirrored from test_events.py)
# ---------------------------------------------------------------------------

_KNOWN_TOKENS = 99


def _make_stream_fn_with_usage() -> Any:
    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            msg = AssistantMessage(
                content=(TextContent(text="ok"),),
                usage=Usage(total_tokens=_KNOWN_TOKENS),
            )
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


def _make_noop_stream_fn() -> Any:
    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            raise RuntimeError("should not be called")

        return _stream_fn

    return _factory


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------


def _script_then_agent_wf() -> WorkflowDef:
    return WorkflowDef(
        name="test",
        provider="fake",
        entry="s",
        default_model="m1",
        nodes=(
            NodeDef(
                id="s",
                type="script",
                code="async def run(ctx): return 'hello'",
                output_ports=(Port("out"),),
            ),
            NodeDef(id="a", model="m1", prompt="go", output_ports=(Port("result"),)),
        ),
        edges=(
            EdgeDef(src="$in", dst="s", mapping=()),
            EdgeDef(src="s", dst="a", mapping=(("out", "out"),)),
        ),
    )


def _loop_wf() -> WorkflowDef:
    """Node 'counter' runs twice via loop_max=2."""
    return WorkflowDef(
        name="loop",
        provider="fake",
        entry="counter",
        default_model="m",
        nodes=(
            NodeDef(
                id="counter",
                type="script",
                code="async def run(ctx): return 'tick'",
                output_ports=(Port("out"),),
            ),
        ),
        edges=(
            EdgeDef(src="$in", dst="counter", mapping=()),
            EdgeDef(src="counter", dst="counter", mapping=(), loop_max=1),
        ),
    )


def _failing_wf() -> WorkflowDef:
    return WorkflowDef(
        name="fail",
        provider="fake",
        entry="f",
        default_model="m",
        nodes=(
            NodeDef(
                id="f",
                type="script",
                code="async def run(ctx): raise RuntimeError('boom')",
                output_ports=(Port("out"),),
            ),
        ),
        edges=(EdgeDef(src="$in", dst="f", mapping=()),),
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


async def test_simple_run_aggregates_per_node() -> None:
    mc = MetricsCollector()
    wf = _script_then_agent_wf()
    await execute(wf, stream_fn_factory=_make_stream_fn_with_usage(), on_event=mc)
    snap = mc.snapshot()

    assert len(snap.nodes) == 2
    s_m = next(n for n in snap.nodes if n.node_id == "s")
    a_m = next(n for n in snap.nodes if n.node_id == "a")

    assert s_m.runs == 1
    assert s_m.failures == 0
    assert s_m.total_duration_s >= 0.0

    assert a_m.runs == 1
    assert a_m.failures == 0
    assert a_m.total_tokens == _KNOWN_TOKENS


async def test_loop_counts_iterations() -> None:
    mc = MetricsCollector()
    wf = _loop_wf()
    await execute(wf, stream_fn_factory=_make_noop_stream_fn(), on_event=mc)
    snap = mc.snapshot()

    counter_m = next(n for n in snap.nodes if n.node_id == "counter")
    assert counter_m.runs >= 2
    # Each run contributes to total_duration_s
    assert counter_m.total_duration_s >= 0.0
    # avg_duration_s is non-negative
    assert counter_m.avg_duration_s >= 0.0


async def test_failures_counted() -> None:
    mc = MetricsCollector()
    wf = _failing_wf()
    with pytest.raises(RuntimeError):
        await execute(wf, stream_fn_factory=_make_noop_stream_fn(), on_event=mc)

    snap = mc.snapshot()
    f_m = next(n for n in snap.nodes if n.node_id == "f")
    assert f_m.failures >= 1
    assert snap.total_failures == f_m.failures


async def test_run_level_totals() -> None:
    mc = MetricsCollector()
    wf = _script_then_agent_wf()
    await execute(wf, stream_fn_factory=_make_stream_fn_with_usage(), on_event=mc)
    snap = mc.snapshot()

    assert snap.total_runs == sum(n.runs for n in snap.nodes)
    assert snap.total_tokens == sum(n.total_tokens for n in snap.nodes)
    assert snap.total_failures == sum(n.failures for n in snap.nodes)
    assert snap.total_duration_s == sum(n.total_duration_s for n in snap.nodes)


async def test_deterministic_order() -> None:
    mc = MetricsCollector()
    wf = _script_then_agent_wf()
    await execute(wf, stream_fn_factory=_make_stream_fn_with_usage(), on_event=mc)
    snap = mc.snapshot()
    ids = [n.node_id for n in snap.nodes]
    # first-seen order matches execution order: s then a
    assert ids == ["s", "a"]


def test_unit_call_directly() -> None:
    """Unit test __call__ without executor."""
    mc = MetricsCollector()
    mc(NodeStarted(node_id="x", step=0))
    mc(NodeFinished(node_id="x", step=0, duration_s=1.5, tokens=10))
    mc(NodeFailed(node_id="x", step=1, duration_s=0.5, error="Err"))
    snap = mc.snapshot()
    assert len(snap.nodes) == 1
    nm = snap.nodes[0]
    assert nm.runs == 1
    assert nm.failures == 1
    assert nm.total_duration_s == pytest.approx(2.0)
    assert nm.total_tokens == 10
    assert nm.avg_duration_s == pytest.approx(2.0)  # total_duration_s / runs = 2.0 / 1


def test_avg_duration_no_runs() -> None:
    mc = MetricsCollector()
    mc(NodeStarted(node_id="y", step=0))
    snap = mc.snapshot()
    assert snap.nodes[0].avg_duration_s == 0.0
