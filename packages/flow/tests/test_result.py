"""Structured workflow process result envelope."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from xdog.flow.result import build_run_result, render_run_result


def test_build_run_result_success_shape() -> None:
    result = build_run_result(
        success=True,
        message="Workflow completed",
        output={"answer": 42},
        workflow="demo",
        run_id="run-1",
        start_time=100.0,
        end_time=100.125,
        tokens_used=7,
        last_node="finish",
    )
    assert result == {
        "success": True,
        "message": "Workflow completed",
        "output": {"answer": 42},
        "context": {
            "workflow": "demo",
            "runId": "run-1",
            "startTime": "1970-01-01T00:01:40Z",
            "endTime": "1970-01-01T00:01:40.125000Z",
            "durationMs": 125,
            "tokensUsed": 7,
            "lastNode": "finish",
        },
    }


def test_rendered_result_builder_is_standalone() -> None:
    source = render_run_result()
    assert "import flow" not in source and "from flow" not in source
    namespace: dict[str, object] = {"datetime": datetime, "UTC": UTC, "Callable": Callable, "Any": Any}
    exec(compile(source, "<result-builder>", "exec"), namespace)  # noqa: S102
    assert callable(namespace["build_run_result"])


def test_stopped_by_is_omitted_when_a_run_simply_finishes() -> None:
    """The field explains an early stop; a clean finish has nothing to explain."""
    result = build_run_result(
        success=True, message="ok", output={}, workflow="w", run_id=None,
        start_time=0.0, end_time=1.0, tokens_used=0, last_node="n",
    )
    context = result["context"]
    assert isinstance(context, dict)
    assert "stoppedBy" not in context


def test_stopped_by_names_the_reason_and_edge() -> None:
    result = build_run_result(
        success=True, message="ok", output={}, workflow="w", run_id=None,
        start_time=0.0, end_time=1.0, tokens_used=0, last_node="fix",
        stopped_by={"reason": "loop_exhausted", "edge": "fix->gate"},
    )
    context = result["context"]
    assert isinstance(context, dict)
    assert context["stoppedBy"] == {"reason": "loop_exhausted", "edge": "fix->gate"}
