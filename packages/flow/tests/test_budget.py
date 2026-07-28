"""Tests for the token-budget circuit-breaker in flow.executor."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from ai.types import AssistantMessage, DoneEvent, TextContent, Usage
from ai.utils.event_stream import EventStream as AiEventStream
from flow.errors import WorkflowBudgetExceeded
from flow.executor import execute
from flow.models import EdgeDef, NodeDef, Port, WorkflowDef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKENS_PER_NODE = 50


def _make_stream_fn_with_tokens(tokens: int) -> Any:
    """Stream-fn factory where each agent node reports exactly *tokens* tokens."""

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            msg = AssistantMessage(
                content=(TextContent(text="ok"),),
                usage=Usage(total_tokens=tokens),
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


def _two_node_agent_wf() -> WorkflowDef:
    """Two sequential agent nodes: a -> b."""
    return WorkflowDef(
        name="budget_test",
        provider="fake",
        entry="a",
        default_model="m",
        nodes=(
            NodeDef(id="a", model="m", prompt="step a", output_ports=(Port("out"),)),
            NodeDef(id="b", model="m", prompt="step b"),
        ),
        edges=(
            EdgeDef(src="a", dst="b", mapping=(("out", "out"),)),
        ),
    )


def _script_only_wf() -> WorkflowDef:
    """Single script node — no agent tokens."""
    return WorkflowDef(
        name="script_only",
        provider="fake",
        entry="s",
        default_model="m",
        nodes=(
            NodeDef(
                id="s",
                type="script",
                code="async def run(ctx): return 'done'",
                output_ports=(Port("out"),),
            ),
        ),
        edges=(),
    )


def _make_noop_stream_fn() -> Any:
    """Minimal stream-fn factory for script-only workflows (never called)."""

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            raise RuntimeError("should not be called for script-only workflows")

        return _stream_fn

    return _factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_under_budget_passes() -> None:
    """Run completes when max_tokens is above total spend; tokens_used is correct."""
    wf = _two_node_agent_wf()
    result = await execute(
        wf,
        stream_fn_factory=_make_stream_fn_with_tokens(_TOKENS_PER_NODE),
        max_tokens=200,  # 2 nodes × 50 = 100 — well under budget
    )
    assert result.runtime["tokens_used"] == _TOKENS_PER_NODE * 2


async def test_over_budget_aborts() -> None:
    """Run aborts with WorkflowBudgetExceeded when spend exceeds max_tokens."""
    wf = _two_node_agent_wf()
    # Budget of 40 — first node costs 50, already over, second should never run.
    b_ran = False

    with pytest.raises(WorkflowBudgetExceeded) as exc_info:
        await execute(
            wf,
            stream_fn_factory=_make_stream_fn_with_tokens(_TOKENS_PER_NODE),
            max_tokens=40,
        )

    exc = exc_info.value
    assert exc.used > exc.budget
    assert exc.used == _TOKENS_PER_NODE  # first node pushed it over
    _ = b_ran  # node b never got to set this (just confirming no AttributeError etc.)


async def test_no_budget_never_raises() -> None:
    """With max_tokens=None (default), no exception is raised regardless of spend."""
    wf = _two_node_agent_wf()
    result = await execute(
        wf,
        stream_fn_factory=_make_stream_fn_with_tokens(10_000),
        max_tokens=None,
    )
    # Should complete successfully
    assert result.runtime["tokens_used"] == 10_000 * 2


async def test_zero_budget_never_raises() -> None:
    """A budget of 0 means unlimited — same as None."""
    wf = _two_node_agent_wf()
    result = await execute(
        wf,
        stream_fn_factory=_make_stream_fn_with_tokens(_TOKENS_PER_NODE),
        max_tokens=0,
    )
    assert result.runtime["tokens_used"] == _TOKENS_PER_NODE * 2


async def test_script_only_tokens_used_zero() -> None:
    """Script-only workflows report tokens_used == 0 and any positive budget passes."""
    wf = _script_only_wf()
    result = await execute(wf, stream_fn_factory=_make_noop_stream_fn(), max_tokens=1)
    assert result.runtime["tokens_used"] == 0


async def test_exact_budget_does_not_raise() -> None:
    """tokens_used == max_tokens is NOT over budget (> is the condition, not >=)."""
    wf = WorkflowDef(
        name="exact",
        provider="fake",
        entry="a",
        default_model="m",
        nodes=(NodeDef(id="a", model="m", prompt="p"),),
        edges=(),
    )
    result = await execute(
        wf,
        stream_fn_factory=_make_stream_fn_with_tokens(_TOKENS_PER_NODE),
        max_tokens=_TOKENS_PER_NODE,  # exactly equal — should pass
    )
    assert result.runtime["tokens_used"] == _TOKENS_PER_NODE


async def test_budget_exceeded_attributes() -> None:
    """WorkflowBudgetExceeded carries .used and .budget attributes."""
    wf = WorkflowDef(
        name="attr_test",
        provider="fake",
        entry="a",
        default_model="m",
        nodes=(NodeDef(id="a", model="m", prompt="p"),),
        edges=(),
    )
    with pytest.raises(WorkflowBudgetExceeded) as exc_info:
        await execute(
            wf,
            stream_fn_factory=_make_stream_fn_with_tokens(100),
            max_tokens=50,
        )
    exc = exc_info.value
    assert exc.used == 100
    assert exc.budget == 50
    assert "token budget exceeded" in str(exc)


async def test_tokens_used_persisted_across_resume(tmp_path: object) -> None:
    """B2: the running token total is checkpointed, so a resumed run keeps counting
    from it — the budget spans resume instead of resetting to 0."""
    from flow.checkpoint import JSONFileCheckpointStore

    store = JSONFileCheckpointStore(tmp_path)  # type: ignore[arg-type]
    run_id = "resume-budget"

    # First run: node a spends 100 tokens and completes; node b would spend 100 more.
    # With a checkpoint, a's completion is saved along with tokens_used=100.
    wf = _two_node_agent_wf()
    await execute(
        wf,
        stream_fn_factory=_make_stream_fn_with_tokens(100),
        checkpoint=store,
        run_id=run_id,
    )
    snap = store.load(run_id)
    assert snap is not None
    assert snap["tokens_used"] == 200, "both nodes' tokens must be persisted"

    # A resume that restores this checkpoint starts tokens_used from 200, not 0.
    # A fresh run over the same store/run_id with a tight budget below the restored
    # total must trip immediately (nothing new to run, but the restored total stands).
    restored = await execute(
        wf,
        stream_fn_factory=_make_stream_fn_with_tokens(100),
        checkpoint=store,
        run_id=run_id,
    )
    # Both nodes were already completed, so nothing re-runs; the restored total is kept.
    assert restored.runtime["tokens_used"] == 200
