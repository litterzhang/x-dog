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
    stopped_by: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build the stable JSON envelope printed at workflow process boundaries.

    ``lastNode`` is the node that completed last. It is descriptive, not
    authoritative: under concurrency a settled batch has no defined ordering, so
    which of several peers reports last is not stable.

    ``stoppedBy`` is the authoritative account of *why* the run ended, and is
    omitted when it simply ran out of work. It exists because a bounded ``loop``
    that hits its limit stops silently — same ``success: true``, possibly the same
    empty ``output`` — and is otherwise indistinguishable from a clean finish.
    """
    context: dict[str, object] = {
        "workflow": workflow,
        "runId": run_id,
        "startTime": _iso_time(start_time),
        "endTime": _iso_time(end_time),
        "durationMs": max(0, round((end_time - start_time) * 1000)),
        "tokensUsed": tokens_used,
        "lastNode": last_node,
    }
    if stopped_by:
        context["stoppedBy"] = stopped_by
    return {
        "success": success,
        "message": message,
        "output": output,
        "context": context,
    }


def render_run_result() -> str:
    """Return the exact standalone result-envelope helpers for codegen."""
    return "\n\n".join(inspect.getsource(fn) for fn in (_iso_time, build_run_result))
