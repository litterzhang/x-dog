"""Single-slot background job runner for the HaveFun page.

Runs at most one workflow at a time in a background thread; a second request
while one is running is refused so the public host can't be made to spawn
unbounded LLM work.  Uploaded workflows are sanitised first (script nodes are a
remote-code-execution surface), so only agent nodes and an allowlisted set of
``run:`` refs are permitted; built-in examples ship in the repo and are trusted.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from flow.executor import execute
from flow.models import WorkflowDef

# Per-run wall-clock ceiling (seconds) handed to execute(timeout=...).
_RUN_TIMEOUT = 900.0


@dataclass
class Job:
    """State of a single workflow run."""

    id: str
    state: str = "running"  # running | done | error
    result: dict[str, dict[str, str]] | None = None
    error: str | None = None
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
        try:
            result = asyncio.run(
                execute(
                    wf,
                    stream_fn_factory=stream_fn_factory,
                    web_search_fn_factory=web_search_fn_factory,
                    timeout=_RUN_TIMEOUT,
                    base_dir=base_dir,
                    inputs=inputs,
                )
            )
            job.result = result.outputs
            job.state = "done"
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            job.error = f"{type(exc).__name__}: {exc}"
            job.state = "error"
        finally:
            job.finished = time.monotonic()
            self._current = None


# Process-wide single runner.
runner = JobRunner()
