"""Public workflow run-result envelope shared by CLI and generated modules."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime


def _iso_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


def build_run_result(
    *,
    success: bool,
    message: str,
    output: dict[str, object],
    workflow: str,
    run_id: str | None,
    start_time: float,
    end_time: float,
    tokens_used: int,
    last_node: str,
) -> dict[str, object]:
    """Build the stable JSON envelope printed at workflow process boundaries."""
    return {
        "success": success,
        "message": message,
        "output": output,
        "context": {
            "workflow": workflow,
            "runId": run_id,
            "startTime": _iso_time(start_time),
            "endTime": _iso_time(end_time),
            "durationMs": max(0, round((end_time - start_time) * 1000)),
            "tokensUsed": tokens_used,
            "lastNode": last_node,
        },
    }


def render_run_result() -> str:
    """Return the exact standalone result-envelope helpers for codegen."""
    return "\n\n".join(inspect.getsource(fn) for fn in (_iso_time, build_run_result))
