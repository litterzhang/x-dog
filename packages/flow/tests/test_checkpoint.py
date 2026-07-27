"""Tests for flow.checkpoint — CheckpointStore and executor resume behaviour."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from flow.checkpoint import JSONFileCheckpointStore
from flow.executor import ExecResult, execute
from flow.models import EdgeDef, NodeDef, Port, WorkflowDef

# ---------------------------------------------------------------------------
# JSONFileCheckpointStore unit tests
# ---------------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = JSONFileCheckpointStore(tmp_path)
    snap: dict[str, Any] = {"outputs": {"$in": {"x": "1"}}, "completed": ["node1"], "stack": [], "out_live": {}}
    store.save("run-abc", snap)
    loaded = store.load("run-abc")
    assert loaded == snap


def test_load_missing_returns_none(tmp_path: Path) -> None:
    store = JSONFileCheckpointStore(tmp_path)
    assert store.load("nonexistent") is None


def test_save_writes_file(tmp_path: Path) -> None:
    store = JSONFileCheckpointStore(tmp_path)
    store.save("run-xyz", {"completed": []})
    target = tmp_path / "run-xyz.json"
    assert target.exists()
    with target.open() as fh:
        data = json.load(fh)
    assert data == {"completed": []}


def test_save_creates_dir(tmp_path: Path) -> None:
    new_dir = tmp_path / "sub" / "dir"
    store = JSONFileCheckpointStore(new_dir)
    store.save("r1", {"ok": True})
    assert (new_dir / "r1.json").exists()


# ---------------------------------------------------------------------------
# Executor resume test — skip already-completed nodes
# ---------------------------------------------------------------------------


def test_resume_skips_completed_nodes(tmp_path: Path) -> None:
    """Run-1 completes node1 then fails; run-2 resumes and skips node1."""
    counter_file = tmp_path / "node1_count.txt"
    counter_file.write_text("0")
    asyncio.run(_run_resume_test(tmp_path, counter_file))


async def _run_resume_test(tmp_path: Path, counter_file: Path) -> None:
    store = JSONFileCheckpointStore(tmp_path / "ckpts")
    run_id = "test-resume"

    counter_path_repr = repr(str(counter_file))

    node1_code = f"""
def node1(ctx, x: str) -> str:
    _p = {counter_path_repr}
    _n = int(open(_p).read().strip()) + 1
    open(_p, 'w').write(str(_n))
    return "node1_out"
"""

    node2_fail_code = """
def node2(ctx, val: str) -> str:
    raise RuntimeError("intentional failure")
"""

    wf = WorkflowDef(
        name="resume_test",
        provider="fake",
        entry="node1",
        default_model="unused",
        initial_state=[("x", "hello")],
        nodes=(
            NodeDef(
                id="node1",
                type="script",
                code=node1_code,
                input_ports=(Port("x"),),
                output_ports=(Port("result"),),
            ),
            NodeDef(
                id="node2",
                type="script",
                code=node2_fail_code,
                input_ports=(Port("val"),),
                output_ports=(Port("final"),),
            ),
        ),
        edges=(
            EdgeDef(src="$in", dst="node1", mapping=(("x", "x"),)),
            EdgeDef(src="node1", dst="node2", mapping=(("result", "val"),)),
        ),
    )

    # Run 1: should complete node1 (checkpoint saved) then fail on node2.
    with pytest.raises(RuntimeError, match="intentional failure"):
        await execute(wf, checkpoint=store, run_id=run_id, stream_fn_factory=lambda m: (lambda *a, **k: None))

    node1_count = int(counter_file.read_text().strip())
    assert node1_count == 1, "node1 should have run exactly once in attempt 1"

    snap = store.load(run_id)
    assert snap is not None
    assert "node1" in snap["completed"]

    node2_ok_code = """
def node2(ctx, val: str) -> str:
    return "done_" + val
"""

    wf2 = WorkflowDef(
        name="resume_test",
        provider="fake",
        entry="node1",
        default_model="unused",
        initial_state=[("x", "hello")],
        nodes=(
            NodeDef(
                id="node1",
                type="script",
                code=node1_code,
                input_ports=(Port("x"),),
                output_ports=(Port("result"),),
            ),
            NodeDef(
                id="node2",
                type="script",
                code=node2_ok_code,
                input_ports=(Port("val"),),
                output_ports=(Port("final"),),
            ),
        ),
        edges=(
            EdgeDef(src="$in", dst="node1", mapping=(("x", "x"),)),
            EdgeDef(src="node1", dst="node2", mapping=(("result", "val"),)),
        ),
    )

    # Run 2: node1 should be skipped (checkpoint), node2 should run.
    result = await execute(wf2, checkpoint=store, run_id=run_id, stream_fn_factory=lambda m: (lambda *a, **k: None))
    assert isinstance(result, ExecResult)

    node1_count_after = int(counter_file.read_text().strip())
    assert node1_count_after == 1, "node1 should NOT re-run on resume"

    state = result.runtime["state"]
    assert state["node2"]["final"] == "done_node1_out"
