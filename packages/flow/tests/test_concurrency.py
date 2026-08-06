"""Tests for P4.2 — concurrency caps."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from xdog.flow.codegen import generate
from xdog.flow.errors import WorkflowValidationError
from xdog.flow.executor import execute
from xdog.flow.loader import parse_workflow
from xdog.flow.models import EdgeDef, NodeDef, WorkflowDef
from xdog.flow.runtime import RuntimeContext

# ---------------------------------------------------------------------------
# Concurrency-measuring infrastructure
# ---------------------------------------------------------------------------


def _make_counter_fn(
    counter: list[int], peak: list[int], delay: float = 0.05
) -> Callable[..., Awaitable[None]]:
    """Return an async script-node callable that tracks peak concurrent executions."""

    async def _node(ctx: RuntimeContext) -> None:
        counter[0] += 1
        if counter[0] > peak[0]:
            peak[0] = counter[0]
        await asyncio.sleep(delay)
        counter[0] -= 1

    return _node


def _make_fanout_wf(n_middle: int, max_concurrency: int = 0) -> WorkflowDef:
    """Build start -> n1..n_middle -> end fan-out workflow (script nodes via 'run' refs)."""
    middle_nodes = [NodeDef(id=f"n{i}", type="script", run="dummy:node") for i in range(1, n_middle + 1)]
    start = NodeDef(id="start", type="script", run="dummy:noop")
    end = NodeDef(id="end", type="script", run="dummy:noop")
    nodes = (start, *middle_nodes, end)

    edges = [EdgeDef(src="start", dst=f"n{i}") for i in range(1, n_middle + 1)]
    edges += [EdgeDef(src=f"n{i}", dst="end") for i in range(1, n_middle + 1)]

    return WorkflowDef(
        name="fanout",
        provider="copilot",
        entry="start",
        nodes=tuple(nodes),
        edges=tuple(edges),
        max_concurrency=max_concurrency,
    )


def _make_resolver(
    counter_fn: Callable[..., Awaitable[None]],
) -> Callable[[str], Callable[..., Awaitable[Any]]]:
    """Return a script_resolver that injects counter_fn for 'dummy:node' and a noop for 'dummy:noop'."""

    async def _noop(ctx: RuntimeContext) -> None:
        pass

    def resolver(run: str) -> Callable[..., Awaitable[Any]]:
        if run == "dummy:node":
            return counter_fn
        return _noop

    return resolver


# ---------------------------------------------------------------------------
# Tests: cap bounds concurrency
# ---------------------------------------------------------------------------


def test_no_cap_completes() -> None:
    """Without a cap the workflow runs to completion."""
    counter: list[int] = [0]
    peak: list[int] = [0]
    wf = _make_fanout_wf(4)
    counter_fn = _make_counter_fn(counter, peak)
    result = asyncio.run(execute(wf, script_resolver=_make_resolver(counter_fn)))
    assert result.runtime["ctx"]["workflow_name"] == "fanout"


def test_cap_bounds_concurrency() -> None:
    """With max_concurrency=2 the peak observed concurrent executions <= 2."""
    counter: list[int] = [0]
    peak: list[int] = [0]
    wf = _make_fanout_wf(6, max_concurrency=2)
    counter_fn = _make_counter_fn(counter, peak)
    asyncio.run(execute(wf, script_resolver=_make_resolver(counter_fn)))
    assert peak[0] <= 2, f"Peak concurrency {peak[0]} exceeded cap of 2"


def test_uncapped_peak_exceeds_two() -> None:
    """Without a cap, running 6 sleepy nodes concurrently should peak > 2."""
    counter: list[int] = [0]
    peak: list[int] = [0]
    wf = _make_fanout_wf(6)  # no cap
    counter_fn = _make_counter_fn(counter, peak, delay=0.05)
    asyncio.run(execute(wf, script_resolver=_make_resolver(counter_fn)))
    # With 6 concurrent nodes and 50ms sleep, peak must be > 2
    assert peak[0] > 2, f"Expected uncapped peak > 2, got {peak[0]}"


def test_execute_param_overrides_workflow_field() -> None:
    """execute(max_concurrency=1) overrides wf.max_concurrency=5."""
    counter: list[int] = [0]
    peak: list[int] = [0]
    wf = _make_fanout_wf(6, max_concurrency=5)
    counter_fn = _make_counter_fn(counter, peak)
    asyncio.run(execute(wf, script_resolver=_make_resolver(counter_fn), max_concurrency=1))
    assert peak[0] <= 1, f"Peak concurrency {peak[0]} exceeded override cap of 1"


def test_execute_param_zero_removes_cap() -> None:
    """execute(max_concurrency=0) removes the cap regardless of workflow field."""
    counter: list[int] = [0]
    peak: list[int] = [0]
    wf = _make_fanout_wf(6, max_concurrency=1)  # workflow says cap=1
    counter_fn = _make_counter_fn(counter, peak, delay=0.05)
    asyncio.run(execute(wf, script_resolver=_make_resolver(counter_fn), max_concurrency=0))
    # With cap removed, 6 concurrent nodes should peak > 1
    assert peak[0] > 1, f"Expected uncapped peak > 1, got {peak[0]}"


# ---------------------------------------------------------------------------
# Loader validation
# ---------------------------------------------------------------------------


def test_loader_rejects_negative_max_concurrency() -> None:
    data: dict[str, Any] = {
        "name": "w",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "script", "code": "async def a(ctx): pass"}],
        "edges": [],
        "max_concurrency": -1,
    }
    with pytest.raises(WorkflowValidationError, match="max_concurrency"):
        parse_workflow(data)


def test_loader_rejects_non_int_max_concurrency() -> None:
    data: dict[str, Any] = {
        "name": "w",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "script", "code": "async def a(ctx): pass"}],
        "edges": [],
        "max_concurrency": "two",
    }
    with pytest.raises(WorkflowValidationError, match="max_concurrency"):
        parse_workflow(data)


def test_loader_accepts_zero_max_concurrency() -> None:
    data: dict[str, Any] = {
        "name": "w",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "script", "code": "async def a(ctx): pass"}],
        "edges": [],
        "max_concurrency": 0,
    }
    wf = parse_workflow(data)
    assert wf.max_concurrency == 0


def test_loader_accepts_positive_max_concurrency() -> None:
    data: dict[str, Any] = {
        "name": "w",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "script", "code": "async def a(ctx): pass"}],
        "edges": [],
        "max_concurrency": 3,
    }
    wf = parse_workflow(data)
    assert wf.max_concurrency == 3


# ---------------------------------------------------------------------------
# Codegen
# ---------------------------------------------------------------------------


def _make_parallel_wf(max_concurrency: int = 0) -> WorkflowDef:
    """Simple a -> (b, c) -> d workflow for codegen checks."""
    return WorkflowDef(
        name="parallel",
        provider="copilot",
        entry="a",
        nodes=(
            NodeDef(id="a", type="script", code="async def a(ctx): pass"),
            NodeDef(id="b", type="script", code="async def b(ctx): pass"),
            NodeDef(id="c", type="script", code="async def c(ctx): pass"),
            NodeDef(id="d", type="script", code="async def d(ctx): pass"),
        ),
        edges=(
            EdgeDef(src="a", dst="b"),
            EdgeDef(src="a", dst="c"),
            EdgeDef(src="b", dst="d"),
            EdgeDef(src="c", dst="d"),
        ),
        max_concurrency=max_concurrency,
    )


def test_codegen_with_cap_emits_sem_and_capped() -> None:
    wf = _make_parallel_wf(max_concurrency=3)
    src = generate(wf)
    assert "_SEM" in src
    assert "_capped" in src
    assert "_capped(node_b(provider))" in src or "_capped(" in src


def test_codegen_without_cap_does_not_emit_sem() -> None:
    wf = _make_parallel_wf(max_concurrency=0)
    src = generate(wf)
    assert "_SEM" not in src
    assert "_capped" not in src
