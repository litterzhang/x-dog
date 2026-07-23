"""Single-slot background job runner for the HaveFun page.

Runs at most one workflow at a time in a background thread; a second request
while one is running is refused so the public host can't be made to spawn
unbounded LLM work.  Uploaded workflows are NOT sanitised — script nodes execute
arbitrary code, so this endpoint trusts its caller; the single-slot guard is the
only throttle.  Built-in examples ship in the repo and are trusted.

Each run's node/loop-level interaction is captured into ``Job.log`` by attaching
a scoped logging handler to the ``flow`` / ``agent`` / ``ai`` loggers for the
duration of the run (the executor has no event hook), so the UI can show the
back-and-forth, not just the final outputs.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from flow.executor import execute
from flow.models import WorkflowDef

# Per-run wall-clock ceiling (seconds) handed to execute(timeout=...).
_RUN_TIMEOUT = 900.0

# Which stdlib loggers to capture, and the curated message prefixes worth showing
# as an execution log (the executor logs these at DEBUG).  Bounds the log to the
# node/loop-level interaction trace rather than the full debug firehose.  Only
# executor prefixes: the loader runs before the job starts (on a different thread)
# and model-sync is background noise, so neither belongs in a run's own log.
_LOG_LOGGERS = ("flow", "agent", "ai")
_LOG_PREFIXES = ("Running node", "Running script node", "Loop edge")
_LOG_CAP = 500  # keep at most this many lines per job


class _JobLogHandler(logging.Handler):
    """Buffers curated log lines into a job's ``log`` list, scoped to one run.

    The handler is attached to the process-wide ``flow`` / ``agent`` / ``ai``
    loggers, so it would otherwise also see records emitted by *other* request
    threads (e.g. a concurrent ``/load`` logging "Loading workflow …").  Because
    each job runs its executor on its own dedicated thread, we filter to records
    emitted on that thread — the only ones that belong to this run.
    """

    def __init__(self, sink: list[str], thread_id: int) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink
        self._thread_id = thread_id
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self._thread_id:
            return  # a record from another request/worker thread — not this run
        msg = record.getMessage()
        if not msg.startswith(_LOG_PREFIXES):
            return
        if len(self._sink) < _LOG_CAP:
            self._sink.append(self.format(record))


@dataclass
class Job:
    """State of a single workflow run."""

    id: str
    state: str = "running"  # running | done | error
    result: dict[str, dict[str, str]] | None = None
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
        handler = _JobLogHandler(job.log, threading.get_ident())
        loggers = [logging.getLogger(name) for name in _LOG_LOGGERS]
        saved_levels = [lg.level for lg in loggers]
        for lg in loggers:
            lg.addHandler(handler)
            if lg.level == logging.NOTSET or lg.level > logging.DEBUG:
                lg.setLevel(logging.DEBUG)
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
            for lg, lvl in zip(loggers, saved_levels, strict=True):
                lg.removeHandler(handler)
                lg.setLevel(lvl)
            job.finished = time.monotonic()
            self._current = None


# Process-wide single runner.
runner = JobRunner()
