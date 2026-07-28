"""Tests for P4.4 — deterministic node memoisation (safe reuse on retry / resume)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from flow.checkpoint import JSONFileCheckpointStore
from flow.codegen import generate
from flow.executor import execute
from flow.loader import parse_workflow
from flow.models import EdgeDef, NodeDef, Port, WorkflowDef
from flow.runtime import RuntimeContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dummy_stream_factory(model: str) -> Any:
    """Dummy stream_fn_factory for script-only workflows (should never be called)."""

    def _stream_fn(model_id: Any, context: Any, options: Any = None) -> Any:
        raise AssertionError("stream_fn should not be called in script-only tests")

    return _stream_fn


def _simple_det_wf(*, deterministic: bool, input_val: str = "hello") -> WorkflowDef:
    """Simple single-node script workflow with deterministic flag controlled by param."""
    node = NodeDef(
        id="n1",
        type="script",
        run="dummy:fn",
        input_ports=(Port(name="x"),),
        output_ports=(Port(name="result"),),
        deterministic=deterministic,
    )
    edge = EdgeDef(src="$in", dst="n1", mapping=(("x", "x"),))
    return WorkflowDef(
        name="det_test",
        provider="fake",
        entry="n1",
        nodes=(node,),
        edges=(edge,),
        initial_state=(("x", input_val),),
    )


def _codegen_wf(*, deterministic: bool, input_val: str = "hello") -> WorkflowDef:
    """Workflow for codegen tests — uses inline code so generate() works."""
    code = (
        "async def run(ctx, x):\n"
        "    return x\n"
    )
    edge_data: list[dict[str, Any]] = [
        {"from": "$in", "to": "n1", "map": {"x": "x"}},
    ]
    return parse_workflow(
        {
            "name": "det_test",
            "provider": "fake",
            "entry": "n1",
            "nodes": [
                {
                    "id": "n1",
                    "type": "script",
                    "code": code,
                    "inputs": ["x"],
                    "outputs": ["result"],
                    "deterministic": deterministic,
                }
            ],
            "edges": edge_data,
            "state": {"x": input_val},
        }
    )


# ---------------------------------------------------------------------------
# Test: deterministic node reused on resume (same input, same run_id)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_reuse_on_resume() -> None:
    """A deterministic node runs once; on re-execute with same store/run_id it is reused."""
    counter: list[int] = [0]

    async def _fn(ctx: RuntimeContext, x: str) -> str:
        counter[0] += 1
        return x

    def resolver(ref: str) -> Any:
        return _fn

    wf = _simple_det_wf(deterministic=True, input_val="hello")

    with tempfile.TemporaryDirectory() as tmp:
        store = JSONFileCheckpointStore(Path(tmp))
        run_id = "run-det-1"

        # First run — node actually runs
        rt1 = await execute(
            wf,
            stream_fn_factory=_dummy_stream_factory,
            script_resolver=resolver,
            checkpoint=store,
            run_id=run_id,
        )
        assert counter[0] == 1
        assert rt1.runtime["state"]["n1"]["result"] == "hello"
        assert rt1.runtime["memo"]  # ledger is populated

        # Second run (same store + run_id) — memo hit, node skipped
        counter[0] = 0
        rt2 = await execute(
            wf,
            stream_fn_factory=_dummy_stream_factory,
            script_resolver=resolver,
            checkpoint=store,
            run_id=run_id,
        )
        assert counter[0] == 0, "deterministic node should not re-execute on resume"
        assert rt2.runtime["state"]["n1"]["result"] == "hello"


# ---------------------------------------------------------------------------
# Test: different input re-runs the deterministic node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_different_input_reruns() -> None:
    """Same deterministic node with a different input gets a new memo entry and re-runs."""
    counter: list[int] = [0]

    async def _fn(ctx: RuntimeContext, x: str) -> str:
        counter[0] += 1
        return x

    def resolver(ref: str) -> Any:
        return _fn

    wf_hello = _simple_det_wf(deterministic=True, input_val="hello")
    wf_world = _simple_det_wf(deterministic=True, input_val="world")

    with tempfile.TemporaryDirectory() as tmp:
        store_a = JSONFileCheckpointStore(Path(tmp) / "a")
        store_b = JSONFileCheckpointStore(Path(tmp) / "b")

        # First run with "hello"
        await execute(
            wf_hello,
            stream_fn_factory=_dummy_stream_factory,
            script_resolver=resolver,
            checkpoint=store_a,
            run_id="run-a",
        )
        assert counter[0] == 1

        # Second run with different input "world" → different memo key → re-runs
        counter[0] = 0
        rt2 = await execute(
            wf_world,
            stream_fn_factory=_dummy_stream_factory,
            script_resolver=resolver,
            checkpoint=store_b,
            run_id="run-b",
        )
        assert counter[0] == 1, "different input must re-run the node"
        assert rt2.runtime["state"]["n1"]["result"] == "world"


# ---------------------------------------------------------------------------
# Test: non-deterministic (default) node runs every time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_deterministic_runs_every_time() -> None:
    """A node without deterministic=True runs every execute call."""
    counter: list[int] = [0]

    async def _fn(ctx: RuntimeContext, x: str) -> str:
        counter[0] += 1
        return x

    def resolver(ref: str) -> Any:
        return _fn

    wf = _simple_det_wf(deterministic=False, input_val="hi")

    with tempfile.TemporaryDirectory() as tmp:
        store = JSONFileCheckpointStore(Path(tmp))

        rt1 = await execute(
            wf,
            stream_fn_factory=_dummy_stream_factory,
            script_resolver=resolver,
            checkpoint=store,
            run_id="run-nd-1",
        )
        assert counter[0] == 1

        # Second run with a fresh run_id (no completed checkpoint) — should re-run
        counter[0] = 0
        rt2 = await execute(
            wf,
            stream_fn_factory=_dummy_stream_factory,
            script_resolver=resolver,
            checkpoint=store,
            run_id="run-nd-2",
        )
        assert counter[0] == 1, "non-deterministic node must run every time (no memo)"
        assert not rt1.runtime["memo"], "non-deterministic node must not populate memo"
        assert not rt2.runtime["memo"]


# ---------------------------------------------------------------------------
# Test: codegen emits _MEMO machinery for deterministic nodes
# ---------------------------------------------------------------------------


def test_codegen_deterministic_emits_memo() -> None:
    """Generated source for a deterministic node contains _MEMO references."""
    wf = _codegen_wf(deterministic=True, input_val="x")
    src = generate(wf)
    assert "_MEMO" in src, "generated module must declare _MEMO"
    assert "import hashlib" in src, "generated module must import hashlib"
    # The per-node memo-key variable should appear only in deterministic nodes
    assert "_MEMO[_mk]" in src, "generated module must store result in _MEMO[_mk]"


def test_codegen_non_deterministic_no_memo_key() -> None:
    """Generated source for a non-deterministic node does NOT contain _MEMO store calls."""
    wf = _codegen_wf(deterministic=False, input_val="x")
    src = generate(wf)
    # The per-node key assignment and store should NOT appear for non-deterministic
    assert "_MEMO[_mk]" not in src, "non-deterministic node must not emit _MEMO[_mk] store"
