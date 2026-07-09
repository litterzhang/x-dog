"""Goal workspace — manages goal.md, status.md, plan.md per goal.

Each goal gets its own directory at ``{goals_dir}/{goal_id}/`` with
three structured markdown files:

- ``goal.md``   — definition, description, verification criteria (immutable)
- ``status.md`` — live task checklist, verification results
- ``plan.md``   — strategy, decisions, adaptations (decision log)

These files are human-readable AND serve as the goal agent's context.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from claw.core.types import (
    Goal, GoalTask, TaskStatus,
    Verification, VerificationMethod, VerificationRun, VerificationResult,
)

logger = logging.getLogger(__name__)


class GoalWorkspace:
    """Manages markdown files in a goal's workspace directory.

    Pure I/O — no LLM calls, no GoalTracker dependency.
    """

    def __init__(self, goals_dir: Path) -> None:
        self._goals_dir = goals_dir

    def goal_dir(self, goal_id: str) -> Path:
        return self._goals_dir / goal_id

    # -- Lifecycle --------------------------------------------------------

    def init_workspace(self, goal: Goal) -> None:
        """Create goal workspace with all three files."""
        d = self.goal_dir(goal.id)
        d.mkdir(parents=True, exist_ok=True)
        self.write_goal(goal)
        self.write_status(goal)
        initial_plan = (
            f"# Plan: {goal.title}\n\n"
            "## Current Strategy\n"
            "(Initial plan pending — goal agent will create one)\n\n"
            "## Decision Log\n"
        )
        self.write_plan(goal.id, initial_plan)

    def remove_workspace(self, goal_id: str) -> None:
        """Remove goal workspace directory."""
        d = self.goal_dir(goal_id)
        if d.exists():
            shutil.rmtree(d)

    # -- goal.md (written once at creation) -------------------------------

    def write_goal(self, goal: Goal) -> None:
        """Write goal.md — definition, description, verification criteria."""
        lines = [f"# {goal.title}", ""]
        lines.append(f"Created: {_format_timestamp(goal.created_at)}")
        lines.append(f"Group: {goal.group_id}")
        lines.append("")

        lines.append("## Description")
        lines.append(goal.description or "(no description)")
        lines.append("")

        lines.append("## Verification")
        lines.extend(_format_verification(goal.verification))
        lines.append("")

        self._write(goal.id, "goal.md", "\n".join(lines))

    def read_goal(self, goal_id: str) -> str:
        """Read goal.md content."""
        return self._read(goal_id, "goal.md")

    # -- status.md (updated on task/verification changes) -----------------

    def write_status(self, goal: Goal) -> None:
        """Write status.md — task checklist and verification results."""
        lines = [f"# Status: {goal.title}", ""]
        lines.append(f"Status: {goal.status}")

        if goal.last_verification_run:
            vr = goal.last_verification_run
            ts = _format_timestamp(vr.timestamp)
            lines.append(f"Last verified: {ts} — {vr.result.upper()}")
            if vr.output:
                lines.append(f"Verification output: {vr.output[:200]}")
        lines.append("")

        lines.append("## Tasks")
        for task in goal.tasks:
            lines.append(_format_task_line(task))
            if task.summary:
                lines.append(f"  Summary: {task.summary}")
        lines.append("")

        self._write(goal.id, "status.md", "\n".join(lines))

    def read_status(self, goal_id: str) -> str:
        """Read status.md content."""
        return self._read(goal_id, "status.md")

    # -- plan.md (strategy + decision log) --------------------------------

    def read_plan(self, goal_id: str) -> str:
        """Read plan.md content."""
        return self._read(goal_id, "plan.md")

    def write_plan(self, goal_id: str, content: str) -> None:
        """Overwrite plan.md entirely."""
        self._write(goal_id, "plan.md", content)

    def append_decision(
        self,
        goal_id: str,
        trigger: str,
        decisions: list[str],
        strategy: str = "",
    ) -> None:
        """Append a decision entry to the Decision Log in plan.md."""
        ts = _format_timestamp()
        lines = [f"### {ts} — {trigger}"]
        for d in decisions:
            lines.append(f"- {d}")
        if strategy:
            lines.append(f"- Strategy: {strategy}")
        lines.append("")

        current = self.read_plan(goal_id)
        updated = current.rstrip() + "\n" + "\n".join(lines) + "\n"
        self.write_plan(goal_id, updated)

    # -- Private I/O ------------------------------------------------------

    def _write(self, goal_id: str, filename: str, content: str) -> None:
        d = self.goal_dir(goal_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(content, encoding="utf-8")

    def _read(self, goal_id: str, filename: str) -> str:
        path = self.goal_dir(goal_id) / filename
        if not path.exists():
            logger.warning("Goal workspace file missing: %s", path)
            return ""
        return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_timestamp(ts: float | None = None) -> str:
    """Format a Unix timestamp as ISO-8601. Uses now if ts is None or 0."""
    if not ts:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_verification(v: Verification) -> list[str]:
    """Format verification criteria for goal.md."""
    lines = [f"Method: {v.method}"]
    if v.method == VerificationMethod.SCRIPT:
        lines.append(f"Script: `{v.script}`")
    elif v.conditions:
        for c in v.conditions:
            lines.append(f"- {c}")
    return lines


def _format_task_line(task: GoalTask) -> str:
    """Format a single task as a markdown checklist line."""
    status_map = {
        TaskStatus.COMPLETED: "[x]",
        TaskStatus.IN_PROGRESS: "[>]",
        TaskStatus.SKIPPED: "[-]",
        TaskStatus.PENDING: "[ ]",
    }
    icon = status_map.get(task.status, "[ ]")
    suffix = f" — {task.status}" if task.status != TaskStatus.PENDING else ""
    line = f"- {icon} [{task.id}] {task.description}{suffix}"
    if task.depends_on:
        line += f" (depends on: {', '.join(task.depends_on)})"
    return line
