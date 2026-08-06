"""Task scheduler for cron/interval/one-off agent runs."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, replace
from pathlib import Path

from xdog.claw.core.types import ScheduledTask, TaskSchedule

logger = logging.getLogger(__name__)

class TaskScheduler:
    """Manages scheduled tasks: load, save, check due tasks."""

    def __init__(self, tasks_file: Path) -> None:
        self._tasks_file = tasks_file
        self._tasks: dict[str, ScheduledTask] = self._load()

    def add_task(self, task: ScheduledTask) -> None:
        self._tasks[task.id] = task
        self._save()

    def remove_task(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        self._save()

    def get_task(self, task_id: str) -> ScheduledTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[ScheduledTask]:
        return list(self._tasks.values())

    def get_due_tasks(self) -> list[ScheduledTask]:
        """Return tasks that are due to run now."""
        now = time.time()
        due = []
        for task in self._tasks.values():
            if not task.enabled:
                continue
            if self._is_due(task, now):
                due.append(task)
        return due

    def mark_run(self, task_id: str) -> None:
        """Mark a task as having been run."""
        task = self._tasks.get(task_id)
        if task:
            self._tasks[task_id] = replace(task, last_run=time.time())
            # Remove one-off tasks after execution
            if task.schedule.run_at is not None:
                self._tasks[task_id] = replace(self._tasks[task_id], enabled=False)
            self._save()

    def _is_due(self, task: ScheduledTask, now: float) -> bool:
        sched = task.schedule
        # One-off: run if run_at <= now and never run before
        if sched.run_at is not None:
            return sched.run_at <= now and task.last_run is None
        # Interval: run if enough time has passed
        if sched.interval_seconds is not None:
            if task.last_run is None:
                return True
            return (now - task.last_run) >= sched.interval_seconds
        # Cron: use croniter if available, fallback to 60s interval
        if sched.cron is not None:
            if task.last_run is None:
                return True
            try:
                import datetime

                from croniter import croniter
                base_dt = datetime.datetime.fromtimestamp(task.last_run, tz=datetime.timezone.utc)
                cron = croniter(sched.cron, base_dt)
                next_fire = cron.get_next(float)
                return now >= next_fire
            except ImportError:
                # croniter not installed — fallback to simple 60s check
                return (now - task.last_run) >= 60
            except Exception:
                return False
        return False

    def _load(self) -> dict[str, ScheduledTask]:
        if not self._tasks_file.exists():
            return {}
        try:
            raw = json.loads(self._tasks_file.read_text(encoding="utf-8"))
            result = {}
            for tid, data in raw.items():
                sched_data = data.pop("schedule", {})
                data["schedule"] = TaskSchedule(**sched_data)
                result[tid] = ScheduledTask(**data)
            return result
        except (json.JSONDecodeError, TypeError):
            return {}

    def _save(self) -> None:
        self._tasks_file.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for tid, task in self._tasks.items():
            d = asdict(task)
            data[tid] = d
        self._tasks_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
