"""Tests for P4.3 human-in-the-loop nodes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from xdog.flow.checkpoint import JSONFileCheckpointStore
from xdog.flow.codegen import generate
from xdog.flow.errors import WorkflowPaused, WorkflowValidationError
from xdog.flow.executor import execute
from xdog.flow.loader import parse_workflow, validate_workflow
from xdog.flow.models import EdgeDef, NodeDef, Port, WorkflowDef

# A no-op stream_fn_factory so tests don't need a real provider.
_FAKE_STREAM_FACTORY = lambda m: (lambda *a, **k: None)  # noqa: E731

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(tmp_path: Path) -> tuple[WorkflowDef, Path]:
    """Build prep(script) -> gate(human) -> finish(script) workflow."""
    counter_file = tmp_path / "prep_count.txt"
    counter_file.write_text("0")

    prep_code = f"""
def prep(ctx) -> str:
    import pathlib
    p = pathlib.Path({str(counter_file)!r})
    n = int(p.read_text().strip()) + 1
    p.write_text(str(n))
    return "prep_done"
"""

    finish_code = """
def finish(ctx, approval: str) -> str:
    return "finished_" + approval
"""

    wf = WorkflowDef(
        name="human_test",
        provider="fake",
        entry="prep",
        nodes=(
            NodeDef(
                id="prep",
                type="script",
                code=prep_code,
                input_ports=(),
                output_ports=(Port("result"),),
            ),
            NodeDef(
                id="gate",
                type="human",
                signal="approval",
                output_ports=(Port("approval"),),
            ),
            NodeDef(
                id="finish",
                type="script",
                code=finish_code,
                input_ports=(Port("approval"),),
                output_ports=(Port("done"),),
            ),
        ),
        edges=(
            EdgeDef(src="prep", dst="gate", mapping=()),
            EdgeDef(src="gate", dst="finish", mapping=(("approval", "approval"),)),
        ),
    )
    return wf, counter_file


# ---------------------------------------------------------------------------
# Pause then resume
# ---------------------------------------------------------------------------


def test_pause_then_resume(tmp_path: Path) -> None:
    asyncio.run(_pause_then_resume(tmp_path))


async def _pause_then_resume(tmp_path: Path) -> None:
    wf, counter_file = _make_workflow(tmp_path)
    store = JSONFileCheckpointStore(tmp_path / "ckpts")
    run_id = "r1"

    # First run — no signals → should raise WorkflowPaused
    with pytest.raises(WorkflowPaused) as exc_info:
        await execute(
            wf, checkpoint=store, run_id=run_id,
            stream_fn_factory=_FAKE_STREAM_FACTORY, workspace=tmp_path,
        )

    paused = exc_info.value
    assert paused.node_id == "gate"
    assert paused.signal == "approval"

    # prep should have run once
    assert int(counter_file.read_text().strip()) == 1

    # A checkpoint should exist
    snap = store.load(run_id)
    assert snap is not None
    assert "prep" in snap["completed"]

    # Second run — signal present → should complete
    result = await execute(
        wf, checkpoint=store, run_id=run_id, signals={"approval"}, stream_fn_factory=_FAKE_STREAM_FACTORY
    )
    assert result.runtime["state"]["finish"]["done"].startswith("finished_")

    # prep must NOT have re-run (counter still 1)
    assert int(counter_file.read_text().strip()) == 1


# ---------------------------------------------------------------------------
# Signal present up front — never pauses
# ---------------------------------------------------------------------------


def test_signal_present_up_front(tmp_path: Path) -> None:
    asyncio.run(_signal_present_up_front(tmp_path))


async def _signal_present_up_front(tmp_path: Path) -> None:
    wf, counter_file = _make_workflow(tmp_path)

    result = await execute(
        wf, signals={"approval"}, stream_fn_factory=_FAKE_STREAM_FACTORY, workspace=tmp_path,
    )
    assert result.runtime["state"]["finish"]["done"].startswith("finished_")
    assert int(counter_file.read_text().strip()) == 1


# ---------------------------------------------------------------------------
# Loader validation
# ---------------------------------------------------------------------------


def test_human_node_requires_signal() -> None:
    raw: dict[str, Any] = {
        "name": "wf",
        "provider": "fake",
        "entry": "gate",
        "nodes": [{"id": "gate", "type": "human"}],
        "edges": [],
    }
    wf = parse_workflow(raw)
    with pytest.raises(WorkflowValidationError, match="non-empty 'signal'"):
        validate_workflow(wf)


def test_human_node_with_code_rejected() -> None:
    raw: dict[str, Any] = {
        "name": "wf",
        "provider": "fake",
        "entry": "gate",
        "nodes": [{"id": "gate", "type": "human", "signal": "approval", "code": "def f(ctx): pass"}],
        "edges": [],
    }
    wf = parse_workflow(raw)
    with pytest.raises(WorkflowValidationError, match="must not set 'code'"):
        validate_workflow(wf)


# ---------------------------------------------------------------------------
# Codegen — human-node workflow generates SystemExit / PAUSED
# ---------------------------------------------------------------------------


def test_codegen_human_node_pauses_without_signal() -> None:
    """Generated module for a human-node workflow raises SystemExit when signal absent."""
    wf = WorkflowDef(
        name="codegen_human_test",
        provider="fake",
        entry="gate",
        nodes=(
            NodeDef(
                id="gate",
                type="human",
                signal="approval",
                output_ports=(Port("approval"),),
            ),
        ),
        edges=(),
    )
    src = generate(wf)

    # The generated source should contain the PAUSED reference
    assert "PAUSED" in src
    assert "approval" in src
    assert "_SIGNALS" in src
    assert "FLOW_SIGNALS" in src
    assert "SystemExit" in src


def test_codegen_no_human_nodes_unchanged() -> None:
    """A workflow with no human nodes still generates valid code (unchanged behaviour)."""
    wf = WorkflowDef(
        name="no_human",
        provider="anthropic",
        entry="step1",
        nodes=(
            NodeDef(
                id="step1",
                type="script",
                code="def step1(ctx) -> str:\n    return 'ok'",
                output_ports=(Port("out"),),
            ),
        ),
        edges=(),
    )
    src = generate(wf)
    assert "_SIGNALS" in src  # present but empty set
    assert "FLOW_SIGNALS" not in src  # not populated from env for non-human workflows
    # No PAUSED / SystemExit runtime logic from a human node (the WorkflowPaused
    # class is inlined into every module, but the pause code path is human-only).
    assert "PAUSED" not in src
    # (SystemExit appears in the generic _drive's control-flow re-raise guard, so
    # it's no longer a human-only marker; PAUSED is the human-pause indicator.)
