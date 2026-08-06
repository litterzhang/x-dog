"""Goal tracker — persistent goal and task management.

Mirrors the scheduler.py pattern: JSON persistence, immutable updates
via dataclasses.replace. Goals persist across session resets and drive
the background goal runner in the gateway daemon.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, replace
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any

from xdog.claw.core.types import (
    Goal,
    GoalNotification,
    GoalStatus,
    GoalTask,
    TaskStatus,
    Verification,
    VerificationMethod,
    VerificationResult,
    VerificationRun,
)

logger = logging.getLogger(__name__)


def _short_hex_id(prefix: str) -> str:
    """Generate a short hex ID like 'g-a1b2' or 't-c3d4'."""
    return f"{prefix}-{os.urandom(2).hex()}"


def _load_verification(data: dict[str, Any] | None) -> Verification:
    """Deserialize a Verification from JSON dict. Returns default if missing."""
    if not data:
        return Verification()
    return Verification(
        method=VerificationMethod(data.get("method", "conditions")),
        script=data.get("script", ""),
        conditions=tuple(data.get("conditions", ())),
    )


def _load_verification_run(data: dict[str, Any] | None) -> VerificationRun | None:
    """Deserialize a VerificationRun from JSON dict. Returns None if missing."""
    if not data:
        return None
    return VerificationRun(
        result=VerificationResult(data.get("result", "failed")),
        output=data.get("output", ""),
        timestamp=data.get("timestamp", 0.0),
    )


def _load_goal_task(data: dict[str, Any]) -> GoalTask:
    """Deserialize a GoalTask from JSON dict with verification fields."""
    v_data = data.pop("verification", None)
    vr_data = data.pop("last_verification_run", None)
    depends_on = data.pop("depends_on", ())
    # Filter to known fields to handle forward-compatibility
    known = {f.name for f in dc_fields(GoalTask)} - {"verification", "last_verification_run", "depends_on"}
    filtered = {k: v for k, v in data.items() if k in known}
    filtered["status"] = TaskStatus(filtered.get("status", "pending"))
    return GoalTask(
        **filtered,
        depends_on=tuple(depends_on) if depends_on else (),
        verification=_load_verification(v_data) if v_data else None,
        last_verification_run=_load_verification_run(vr_data),
    )


class GoalTracker:
    """Manages goals with JSON persistence.

    Storage layout: ``{goals_file}`` contains a JSON object with
    ``goals`` (dict of goal dicts) and ``notifications`` (list of
    notification dicts).
    """

    # Max age for completed/abandoned goals (seconds). Older ones are
    # purged on load to prevent unbounded growth of goals.json.
    _GC_MAX_AGE = 7 * 86400  # 7 days

    def __init__(self, goals_file: Path) -> None:
        self._goals_file = goals_file
        self._goals: dict[str, Goal] = {}
        self._notifications: list[GoalNotification] = []
        self._load()
        self._gc()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_goal(
        self,
        group_id: str,
        title: str,
        description: str,
        task_descriptions: list[str],
        *,
        verification: Verification | None = None,
    ) -> Goal:
        """Create a new goal with tasks and verification criteria."""
        now = time.time()
        goal_id = _short_hex_id("g")
        tasks = tuple(
            GoalTask(id=_short_hex_id("t"), description=desc)
            for desc in task_descriptions
        )
        goal = Goal(
            id=goal_id,
            group_id=group_id,
            title=title,
            description=description,
            tasks=tasks,
            status=GoalStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            verification=verification or Verification(),
        )
        self._goals[goal_id] = goal
        self._save()
        return goal

    def update_task(
        self,
        goal_id: str,
        task_id: str,
        status: TaskStatus,
        *,
        summary: str = "",
        notes: str = "",
    ) -> Goal:
        """Update a task's status within a goal. Returns the updated goal."""
        goal = self._get_or_raise(goal_id)
        if all(t.id != task_id for t in goal.tasks):
            raise ValueError(f"Task {task_id} not found in goal {goal_id}")
        new_tasks = tuple(
            replace(
                t,
                status=status,
                summary=summary if summary else t.summary,
                notes=notes if notes else t.notes,
            )
            if t.id == task_id
            else t
            for t in goal.tasks
        )
        updated = replace(goal, tasks=new_tasks, updated_at=time.time())
        self._goals[goal_id] = updated
        self._save()
        return updated

    def add_task(
        self, goal_id: str, description: str,
        *, depends_on: tuple[str, ...] = (),
    ) -> Goal:
        """Add a new task to an existing goal. Returns the updated goal."""
        goal = self._get_or_raise(goal_id)
        new_task = GoalTask(
            id=_short_hex_id("t"),
            description=description,
            depends_on=depends_on,
        )
        updated = replace(
            goal,
            tasks=(*goal.tasks, new_task),
            updated_at=time.time(),
        )
        self._goals[goal_id] = updated
        self._save()
        return updated

    def replace_tasks(self, goal_id: str, tasks: tuple[GoalTask, ...]) -> Goal:
        """Replace all tasks on a goal. Used to set dependencies after creation."""
        goal = self._get_or_raise(goal_id)
        updated = replace(goal, tasks=tasks, updated_at=time.time())
        self._goals[goal_id] = updated
        self._save()
        return updated

    def update_goal_status(
        self,
        goal_id: str,
        status: GoalStatus,
        *,
        summary: str = "",
    ) -> Goal:
        """Update the overall goal status. Returns the updated goal."""
        goal = self._get_or_raise(goal_id)
        updated = replace(
            goal,
            status=status,
            summary=summary if summary else goal.summary,
            updated_at=time.time(),
        )
        self._goals[goal_id] = updated
        self._save()
        return updated

    def record_verification(
        self,
        goal_id: str,
        run: VerificationRun,
    ) -> Goal:
        """Record a verification run result on a goal."""
        goal = self._get_or_raise(goal_id)
        updated = replace(goal, last_verification_run=run, updated_at=time.time())
        self._goals[goal_id] = updated
        self._save()
        return updated

    def list_goals(self, status_filter: str | None = None) -> list[Goal]:
        """List goals, optionally filtered by status.

        ``status_filter`` accepts ``"active"``, ``"completed"``,
        ``"abandoned"``, or ``"all"`` (default when *None*).
        """
        goals = list(self._goals.values())
        if status_filter and status_filter != "all":
            goals = [g for g in goals if g.status == status_filter]
        return goals

    def get_goal(self, goal_id: str) -> Goal | None:
        """Return a goal by ID, or None if not found."""
        return self._goals.get(goal_id)

    def build_active_summary(self) -> str:
        """Build a markdown summary of active goals for the system prompt."""
        active = [g for g in self._goals.values() if g.status == GoalStatus.ACTIVE]
        if not active:
            return ""
        lines = ["## Active Goals", ""]
        for goal in active:
            lines.append(f"### [{goal.id}] {goal.title}")
            for task in goal.tasks:
                if task.status == TaskStatus.COMPLETED:
                    lines.append(f"- [x] [{task.id}] {task.description}")
                elif task.status == TaskStatus.IN_PROGRESS:
                    lines.append(
                        f"- [ ] [{task.id}] **\u2192 {task.description}** (in_progress)"
                    )
                elif task.status == TaskStatus.SKIPPED:
                    lines.append(f"- [-] [{task.id}] ~~{task.description}~~ (skipped)")
                else:
                    lines.append(f"- [ ] [{task.id}] {task.description}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def build_completed_summary(self, max_age_days: int = 7) -> str:
        """Build a markdown summary of recently completed goals."""
        cutoff = time.time() - (max_age_days * 86400)
        recent = [
            g
            for g in self._goals.values()
            if g.status in (GoalStatus.COMPLETED, GoalStatus.ABANDONED)
            and g.updated_at >= cutoff
        ]
        if not recent:
            return ""
        lines = ["## Recently Completed Goals", ""]
        for goal in recent:
            mark = "\u2713" if goal.status == GoalStatus.COMPLETED else "\u2717"
            lines.append(f"### [{goal.id}] {goal.title} {mark}")
            if goal.summary:
                lines.append(goal.summary)
            lines.append("")
        return "\n".join(lines).rstrip()

    def has_running_tasks(self) -> bool:
        """Return True if any active goal has an in-progress task."""
        return any(
            task.status == TaskStatus.IN_PROGRESS
            for goal in self._goals.values()
            if goal.status == GoalStatus.ACTIVE
            for task in goal.tasks
        )

    # ------------------------------------------------------------------
    # Notification queue
    # ------------------------------------------------------------------

    def add_notification(
        self,
        goal_id: str,
        title: str,
        summary: str,
        status: str,
    ) -> None:
        """Append a notification to the queue (persisted in goals.json)."""
        notif = GoalNotification(
            goal_id=goal_id,
            title=title,
            summary=summary,
            status=status,
            timestamp=time.time(),
        )
        self._notifications.append(notif)
        self._save()

    def pop_notifications(self) -> list[GoalNotification]:
        """Return and clear all pending notifications."""
        if not self._notifications:
            return []
        result = list(self._notifications)
        self._notifications.clear()
        self._save()
        return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _get_or_raise(self, goal_id: str) -> Goal:
        goal = self._goals.get(goal_id)
        if goal is None:
            raise ValueError(f"Goal {goal_id} not found")
        return goal

    def _load(self) -> None:
        if not self._goals_file.exists():
            return
        try:
            raw = json.loads(self._goals_file.read_text(encoding="utf-8"))
            known_goal_fields = {f.name for f in dc_fields(Goal)} - {"verification", "last_verification_run", "tasks"}
            goals_data = raw.get("goals", {})
            for gid, gdata in goals_data.items():
                try:
                    tasks_raw = gdata.pop("tasks", [])
                    tasks = tuple(_load_goal_task(t) for t in tasks_raw)
                    v_data = gdata.pop("verification", None)
                    vr_data = gdata.pop("last_verification_run", None)
                    # Filter to known fields for forward-compatibility
                    filtered = {k: v for k, v in gdata.items() if k in known_goal_fields}
                    filtered["status"] = GoalStatus(filtered.get("status", "active"))
                    self._goals[gid] = Goal(
                        **filtered,
                        tasks=tasks,
                        verification=_load_verification(v_data),
                        last_verification_run=_load_verification_run(vr_data),
                    )
                except (TypeError, ValueError) as exc:
                    logger.warning("Skipping corrupted goal %s: %s", gid, exc)
            notifs_data = raw.get("notifications", [])
            self._notifications = [GoalNotification(**n) for n in notifs_data]
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to load goals file %s: %s", self._goals_file, exc)

    def _save(self) -> None:
        self._goals_file.parent.mkdir(parents=True, exist_ok=True)
        goals_data: dict[str, Any] = {}
        for gid, goal in self._goals.items():
            d = asdict(goal)
            goals_data[gid] = d
        data = {
            "goals": goals_data,
            "notifications": [asdict(n) for n in self._notifications],
        }
        # Atomic write: temp file then os.replace() to prevent corruption
        tmp_path = self._goals_file.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8",
        )
        os.replace(tmp_path, self._goals_file)

    def _gc(self) -> None:
        """Purge completed/abandoned goals older than _GC_MAX_AGE."""
        cutoff = time.time() - self._GC_MAX_AGE
        stale = [
            gid for gid, g in self._goals.items()
            if g.status in (GoalStatus.COMPLETED, GoalStatus.ABANDONED)
            and g.updated_at < cutoff
        ]
        if stale:
            for gid in stale:
                del self._goals[gid]
                logger.info("Garbage collected old goal: %s", gid)
            self._save()


# ---------------------------------------------------------------------------
# Cached factory
# ---------------------------------------------------------------------------

_tracker_cache: dict[str, tuple[float, GoalTracker]] = {}


def get_tracker(goals_file: Path) -> GoalTracker:
    """Get a GoalTracker with mtime-based caching."""
    key = str(goals_file)
    mtime = goals_file.stat().st_mtime if goals_file.exists() else 0
    cached = _tracker_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    tracker = GoalTracker(goals_file)
    _tracker_cache[key] = (mtime, tracker)
    return tracker
