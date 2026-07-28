"""Single-slot background job runner for the HaveFun page.

Runs at most one workflow at a time in a background thread; a second request
while one is running is refused so the public host can't be made to spawn
unbounded LLM work.  Uploaded workflows are NOT sanitised — script nodes execute
arbitrary code, so this endpoint trusts its caller; the single-slot guard is the
only throttle.  Built-in examples ship in the repo and are trusted.

Each run's node-level interaction is captured into ``Job.log`` via flow's own
structured event stream (P3): ``execute(on_event=...)`` delivers typed
NodeStarted / NodeFinished / NodeFailed events — carrying per-node duration and
(for agent nodes) token usage — which we format into human-readable log lines.
No stdlib-logging scraping or thread filtering needed: the callback fires on the
run's own event loop and only for this run's nodes.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from flow.events import FlowEvent, NodeFailed, NodeFinished, NodeStarted
from flow.executor import execute
from flow.models import WorkflowDef

# Per-run wall-clock ceiling (seconds) handed to execute(timeout=...).
_RUN_TIMEOUT = 900.0

_LOG_CAP = 500  # keep at most this many lines per job


def _format_event(ev: FlowEvent) -> str | None:
    """Render a P3 lifecycle event as one human-readable execution-log line."""
    if isinstance(ev, NodeStarted):
        return f"▶ {ev.node_id}"
    if isinstance(ev, NodeFinished):
        tok = f", {ev.tokens} tok" if ev.tokens else ""
        return f"✓ {ev.node_id} ({ev.duration_s:.1f}s{tok})"
    if isinstance(ev, NodeFailed):
        return f"✗ {ev.node_id} ({ev.duration_s:.1f}s): {ev.error}"
    return None


@dataclass
class Job:
    """State of a single workflow run."""

    id: str
    state: str = "running"  # running | done | error
    result: dict[str, str] | None = None  # the workflow's $output map (runtime["out"])
    error: str | None = None
    log: list[str] = field(default_factory=list)
    started: float = 0.0
    finished: float | None = None

    @property
    def elapsed(self) -> float:
        end = self.finished if self.finished is not None else time.monotonic()
        return round(end - self.started, 1)


class JobRunner:
    """Runs at most one workflow at a time; refuses overlapping runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._current: str | None = None

    def is_busy(self) -> bool:
        return self._current is not None and self._jobs[self._current].state == "running"

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def start(
        self,
        wf: WorkflowDef,
        inputs: dict[str, str],
        stream_fn_factory: Callable[[str], Any] | None = None,
        base_dir: Path | None = None,
        web_search_fn_factory: Callable[[str], Any] | None = None,
    ) -> str | None:
        """Start a run; return its job id, or ``None`` if a run is in progress."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if self.is_busy():
                return None
            job_id = secrets.token_hex(8)
            job = Job(id=job_id, started=time.monotonic())
            self._jobs = {job_id: job}  # keep only the latest job
            self._current = job_id
        finally:
            self._lock.release()

        thread = threading.Thread(
            target=self._run,
            args=(job, wf, inputs, stream_fn_factory, base_dir, web_search_fn_factory),
            daemon=True,
        )
        thread.start()
        return job_id

    def _run(
        self,
        job: Job,
        wf: WorkflowDef,
        inputs: dict[str, str],
        stream_fn_factory: Callable[[str], Any] | None,
        base_dir: Path | None,
        web_search_fn_factory: Callable[[str], Any] | None,
    ) -> None:
        def _on_event(ev: FlowEvent) -> None:
            line = _format_event(ev)
            if line is not None and len(job.log) < _LOG_CAP:
                job.log.append(line)

        try:
            result = asyncio.run(
                execute(
                    wf,
                    stream_fn_factory=stream_fn_factory,
                    web_search_fn_factory=web_search_fn_factory,
                    timeout=_RUN_TIMEOUT,
                    base_dir=base_dir,
                    inputs=inputs,
                    on_event=_on_event,
                )
            )
            job.result = result.runtime["out"]
            job.state = "done"
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            job.error = f"{type(exc).__name__}: {exc}"
            job.state = "error"
        finally:
            job.finished = time.monotonic()
            self._current = None


# Process-wide single runner.
runner = JobRunner()
