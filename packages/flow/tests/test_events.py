"""Tests for flow.events — typed lifecycle events and executor integration."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from ai.types import AssistantMessage, DoneEvent, TextContent, Usage
from ai.utils.event_stream import EventStream as AiEventStream
from flow.codegen import generate
from flow.events import EventCallback, FlowEvent, NodeFailed, NodeFinished, NodeStarted
from flow.executor import execute
from flow.models import EdgeDef, NodeDef, Port, WorkflowDef

# ---------------------------------------------------------------------------
# Event dataclass tests
# ---------------------------------------------------------------------------


def test_node_started_frozen() -> None:
    ev = NodeStarted(node_id="a", step=0)
    assert ev.node_id == "a"
    assert ev.step == 0
    with pytest.raises((TypeError, AttributeError)):
        ev.node_id = "b"  # type: ignore[misc]


def test_node_finished_frozen() -> None:
    ev = NodeFinished(node_id="a", step=0, duration_s=1.5, tokens=42)
    assert ev.tokens == 42
    with pytest.raises((TypeError, AttributeError)):
        ev.duration_s = 0.0  # type: ignore[misc]


def test_node_failed_frozen() -> None:
    ev = NodeFailed(node_id="x", step=1, duration_s=0.1, error="ValueError: oops")
    assert ev.error == "ValueError: oops"
    with pytest.raises((TypeError, AttributeError)):
        ev.error = ""  # type: ignore[misc]


def test_node_finished_default_tokens() -> None:
    ev = NodeFinished(node_id="n", step=0, duration_s=0.0)
    assert ev.tokens == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOWN_TOKENS = 99


def _make_stream_fn_with_usage(responses: dict[str, str]) -> Any:
    """Stream-fn factory that returns AssistantMessage with usage.total_tokens == _KNOWN_TOKENS."""

    def _factory(model: str) -> Any:
        text = responses.get(model, "hello")

        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            msg = AssistantMessage(
                content=(TextContent(text=text),),
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
    """Minimal stream-fn factory for script-only workflows (never called)."""

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            raise RuntimeError("should not be called for script-only workflows")

        return _stream_fn

    return _factory


def _two_node_agent_wf() -> WorkflowDef:
    return WorkflowDef(
        name="test",
        provider="fake",
        entry="a",
        default_model="m1",
        nodes=(
            NodeDef(id="a", model="m1", prompt="task-a", output_ports=(Port("out"),)),
            NodeDef(id="b", model="m1", prompt="task-b", output_ports=(Port("result"),)),
        ),
        edges=(
            EdgeDef(src="$in", dst="a", mapping=()),
            EdgeDef(src="a", dst="b", mapping=(("out", "out"),)),
        ),
    )


def _one_script_node_wf() -> WorkflowDef:
    return WorkflowDef(
        name="script_test",
        provider="fake",
        entry="s",
        default_model="m",
        nodes=(
            NodeDef(
                id="s",
                type="script",
                code="async def run(ctx): return 'ok'",
                output_ports=(Port("out"),),
            ),
        ),
        edges=(EdgeDef(src="$in", dst="s", mapping=()),),
    )


def _failing_script_wf() -> WorkflowDef:
    return WorkflowDef(
        name="fail_test",
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
# Interpreter event tests
# ---------------------------------------------------------------------------


async def test_two_agent_nodes_emit_ordered_events() -> None:
    """NodeStarted(a), NodeFinished(a), NodeStarted(b), NodeFinished(b)."""
    events: list[FlowEvent] = []
    cb: EventCallback = events.append

    wf = _two_node_agent_wf()
    await execute(wf, stream_fn_factory=_make_stream_fn_with_usage({"m1": "hi"}), on_event=cb)

    node_ids = [e.node_id for e in events]
    types = [type(e).__name__ for e in events]
    assert types == ["NodeStarted", "NodeFinished", "NodeStarted", "NodeFinished"]
    assert node_ids[0] == node_ids[1] == "a"
    assert node_ids[2] == node_ids[3] == "b"

    for ev in events:
        if isinstance(ev, NodeFinished):
            assert ev.duration_s >= 0


async def test_agent_node_finished_carries_tokens() -> None:
    events: list[FlowEvent] = []
    wf = _two_node_agent_wf()
    await execute(wf, stream_fn_factory=_make_stream_fn_with_usage({"m1": "hi"}), on_event=events.append)

    finished = [e for e in events if isinstance(e, NodeFinished)]
    assert len(finished) == 2
    for ev in finished:
        assert ev.tokens == _KNOWN_TOKENS


async def test_script_node_emits_events() -> None:
    events: list[FlowEvent] = []
    wf = _one_script_node_wf()
    await execute(wf, stream_fn_factory=_make_noop_stream_fn(), on_event=events.append)

    types = [type(e).__name__ for e in events]
    assert types == ["NodeStarted", "NodeFinished"]
    finished = events[1]
    assert isinstance(finished, NodeFinished)
    assert finished.tokens == 0
    assert finished.duration_s >= 0


async def test_failing_node_emits_node_failed_before_raise() -> None:
    events: list[FlowEvent] = []
    wf = _failing_script_wf()
    with pytest.raises(RuntimeError):
        await execute(wf, stream_fn_factory=_make_noop_stream_fn(), on_event=events.append)

    failed = [e for e in events if isinstance(e, NodeFailed)]
    assert len(failed) == 1
    assert failed[0].node_id == "f"
    assert "RuntimeError" in failed[0].error
    assert "boom" in failed[0].error


async def test_no_callback_no_change() -> None:
    """Without on_event the workflow runs exactly as before — result unchanged."""
    wf = _one_script_node_wf()
    result = await execute(wf, stream_fn_factory=_make_noop_stream_fn())
    assert result.runtime["state"]["s"]["out"] == "ok"


async def test_raising_callback_does_not_crash_run() -> None:
    """A callback that raises must not prevent the workflow from completing."""

    def _bad_cb(ev: FlowEvent) -> None:
        raise ValueError("callback bug")

    wf = _one_script_node_wf()
    result = await execute(wf, stream_fn_factory=_make_noop_stream_fn(), on_event=_bad_cb)
    assert result.runtime["state"]["s"]["out"] == "ok"


# ---------------------------------------------------------------------------
# Codegen: generated source contains logging calls
# ---------------------------------------------------------------------------


def test_generated_module_contains_event_log_calls() -> None:
    wf = _one_script_node_wf()
    src = generate(wf)
    assert "_EVENT_LOG" in src
    assert "NodeStarted" in src
    assert "NodeFinished" in src
    assert "NodeFailed" in src
    assert "time.monotonic()" in src


def test_generated_module_contains_logger_definition() -> None:
    wf = _one_script_node_wf()
    src = generate(wf)
    assert 'logging.getLogger("flow.generated.events")' in src


def test_generated_module_logs_on_exec() -> None:
    """Generated source calls _EVENT_LOG.info with NodeStarted/NodeFinished strings."""
    wf = _one_script_node_wf()
    src = generate(wf)
    # Verify the generated source contains the expected logging calls.
    assert "_EVENT_LOG.info('NodeStarted" in src
    assert "_EVENT_LOG.info('NodeFinished" in src
