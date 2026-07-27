"""Tests for P4.1 — per-branch failure isolation."""

from __future__ import annotations

from typing import Any

import pytest
from flow.codegen import generate
from flow.errors import WorkflowValidationError
from flow.executor import execute
from flow.loader import parse_workflow
from flow.models import EdgeDef, NodeDef, Port, WorkflowDef

# A no-op stream_fn_factory for script-only workflows (no agent nodes run).
_NOOP_FACTORY: Any = lambda _model: None  # noqa: E731

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_script_node(
    node_id: str,
    *,
    output_port: str = "out",
    code: str,
    on_error: str = "fail",
) -> NodeDef:
    return NodeDef(
        id=node_id,
        type="script",
        output_ports=(Port(name=output_port),),
        code=code,
        on_error=on_error,  # type: ignore[arg-type]
    )


def _diamond_workflow(bad_on_error: str) -> WorkflowDef:
    """Construct: start -> (good, bad) -> end, where bad may raise."""
    start = _make_script_node(
        "start",
        code="async def start(ctx): return 'started'",
    )
    good = _make_script_node(
        "good",
        code="async def good(ctx): return 'good_result'",
    )
    bad = _make_script_node(
        "bad",
        code="async def bad(ctx): raise ValueError('intentional')",
        on_error=bad_on_error,
    )
    end = _make_script_node(
        "end",
        code="async def end(ctx): return 'end_result'",
    )
    edges = (
        EdgeDef(src="$in", dst="start"),
        EdgeDef(src="start", dst="good"),
        EdgeDef(src="start", dst="bad"),
        EdgeDef(src="good", dst="end"),
        EdgeDef(src="bad", dst="end"),
    )
    return WorkflowDef(
        name="diamond",
        provider="fake",
        entry="start",
        nodes=(start, good, bad, end),
        edges=edges,
    )


# ---------------------------------------------------------------------------
# Executor tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_isolate_keeps_siblings_alive() -> None:
    """When bad has on_error='isolate', good runs to completion; bad is in failed."""
    wf = _diamond_workflow(bad_on_error="isolate")
    result = await execute(wf, timeout=5.0, stream_fn_factory=_NOOP_FACTORY)
    rt = result.runtime

    # good completed
    assert "good" in rt["state"]
    # bad is in failed
    assert "bad" in rt["failed"]
    assert "ValueError" in rt["failed"]["bad"]
    # end did NOT run (depends on bad which was isolated)
    assert "end" not in rt["state"]
    # run did not raise


@pytest.mark.asyncio
async def test_fail_fast_default_unchanged() -> None:
    """Default on_error='fail' should raise and abort the run."""
    wf = _diamond_workflow(bad_on_error="fail")
    with pytest.raises(ValueError, match="intentional"):
        await execute(wf, timeout=5.0, stream_fn_factory=_NOOP_FACTORY)


@pytest.mark.asyncio
async def test_failed_key_empty_when_no_isolation() -> None:
    """runtime['failed'] is always present and empty when no isolation occurs."""
    start = _make_script_node("start", code="async def start(ctx): return 'ok'")
    wf = WorkflowDef(
        name="simple",
        provider="fake",
        entry="start",
        nodes=(start,),
        edges=(EdgeDef(src="$in", dst="start"),),
    )
    result = await execute(wf, timeout=5.0, stream_fn_factory=_NOOP_FACTORY)
    assert result.runtime["failed"] == {}


# ---------------------------------------------------------------------------
# Loader validation test
# ---------------------------------------------------------------------------


def test_loader_rejects_invalid_on_error() -> None:
    """on_error='banana' must raise WorkflowValidationError."""
    data: dict[str, Any] = {
        "name": "test",
        "provider": "openai",
        "entry": "n1",
        "nodes": [{"id": "n1", "type": "script", "on_error": "banana", "code": "async def n1(ctx): return ''"}],
        "edges": [{"from": "$in", "to": "n1"}],
    }
    with pytest.raises(WorkflowValidationError, match="on_error must be"):
        parse_workflow(data)


# ---------------------------------------------------------------------------
# Codegen test
# ---------------------------------------------------------------------------


def test_codegen_isolation_guard_present() -> None:
    """Generated source for an isolate node contains _ISOLATED guard and _FAILED record."""
    bad = _make_script_node(
        "bad",
        code="async def bad(ctx): raise ValueError('x')",
        on_error="isolate",
    )
    wf = WorkflowDef(
        name="cg_test",
        provider="openai",
        entry="bad",
        nodes=(bad,),
        edges=(EdgeDef(src="$in", dst="bad"),),
    )
    src = generate(wf)
    assert "_ISOLATED" in src
    assert "_FAILED" in src
    assert "'bad' in _ISOLATED" in src


def test_codegen_no_isolation_for_default_node() -> None:
    """Default (on_error='fail') nodes still emit plain ``raise``."""
    node = _make_script_node(
        "n1",
        code="async def n1(ctx): return 'x'",
    )
    wf = WorkflowDef(
        name="cg_fail",
        provider="openai",
        entry="n1",
        nodes=(node,),
        edges=(EdgeDef(src="$in", dst="n1"),),
    )
    src = generate(wf)
    # Still has _ISOLATED / _FAILED in the template globals
    assert "_ISOLATED: set[str] = set()" in src
    assert "_FAILED: dict[str, str] = {}" in src
    # The node function should raise, not capture
    assert "        raise" in src


def test_codegen_failed_in_runtime() -> None:
    """Generated module's _RUNTIME dict includes 'failed' key."""
    node = _make_script_node("n1", code="async def n1(ctx): return 'x'")
    wf = WorkflowDef(
        name="cg_rt",
        provider="openai",
        entry="n1",
        nodes=(node,),
        edges=(EdgeDef(src="$in", dst="n1"),),
    )
    src = generate(wf)
    assert '"failed": _FAILED' in src
