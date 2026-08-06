"""Goal tool — create, list, update tasks, add tasks, abandon goals.

Uses ToolDef framework. Integrates with GoalManager's state machine
for deterministic transitions and planner for initial/re-planning.

Note: ``verify`` and ``complete_goal`` actions are NOT exposed to the
main agent — the state machine handles both automatically. The planner
calls the tracker/manager directly during planning.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from xdog.agent.tool_def import Param, ToolDef, action
from xdog.claw.core.types import (
    GoalStatus,
    TaskStatus,
    Verification,
    VerificationMethod,
)

_TASK_STATUS_MAP = {
    "complete": TaskStatus.COMPLETED,
    "skip": TaskStatus.SKIPPED,
}


def _tracker(ctx: dict[str, Any]):
    """Get GoalTracker — prefer GoalManager's tracker, fall back to standalone."""
    manager = ctx.get("_goal_manager")
    if manager is not None:
        return manager.tracker
    from pathlib import Path

    from xdog.claw.core.planning.goal_tracker import get_tracker
    goals_file = Path(ctx["data_dir"]) / "groups" / ctx["group_id"] / "goals.json"
    return get_tracker(goals_file)


def _format_goal(goal) -> str:
    lines = [f"{goal.title} [{goal.id}] — {goal.status}"]
    for t in goal.tasks:
        icon = {"completed": "[x]", "in_progress": "[>]", "skipped": "[-]"}.get(t.status, "[ ]")
        lines.append(f"  {icon} [{t.id}] {t.description}")
        if t.summary:
            lines.append(f"      Summary: {t.summary}")
        if t.depends_on:
            lines.append(f"      Depends on: {', '.join(t.depends_on)}")
    if goal.verification and goal.verification.method:
        lines.append(f"  Verification: {goal.verification.method}")
    if goal.last_verification_run:
        lines.append(f"  Last verified: {goal.last_verification_run.result}")
    return "\n".join(lines)


def _parse_task_input(tasks: Sequence[Any]) -> tuple[list[str], list[list[int]]]:
    """Parse task input that can be strings or objects with depends_on.

    Returns (descriptions, dep_indices) where dep_indices[i] is a list
    of 0-based indices into the tasks array that task i depends on.
    """
    descriptions: list[str] = []
    dep_indices: list[list[int]] = []

    for item in tasks:
        if isinstance(item, str):
            descriptions.append(item)
            dep_indices.append([])
        elif isinstance(item, dict):
            descriptions.append(item.get("description", ""))
            raw_deps = item.get("depends_on", [])
            # depends_on can be indices (int) or index strings ("0", "1")
            indices = []
            for d in raw_deps:
                try:
                    indices.append(int(d))
                except (ValueError, TypeError):
                    pass  # skip invalid refs — will be caught later
            dep_indices.append(indices)
        else:
            descriptions.append(str(item))
            dep_indices.append([])

    return descriptions, dep_indices


def _apply_task_dependencies(
    ctx: dict[str, Any], goal, dep_indices: list[list[int]],
) -> None:
    """Resolve index-based depends_on to real task IDs and update tracker."""
    from dataclasses import replace as dc_replace

    task_ids = [t.id for t in goal.tasks]

    new_tasks = list(goal.tasks)
    changed = False
    for i, indices in enumerate(dep_indices):
        if not indices:
            continue
        resolved = tuple(
            task_ids[idx] for idx in indices
            if 0 <= idx < len(task_ids)
        )
        if resolved:
            new_tasks[i] = dc_replace(new_tasks[i], depends_on=resolved)
            changed = True

    if changed:
        _tracker(ctx).replace_tasks(goal.id, tuple(new_tasks))


class GoalTool(ToolDef):
    name = "goal"
    description = "Manage verifiable goals. Create goals, mark tasks complete, check status."
    required_ctx = ("data_dir", "group_id")

    @action("create", description="Create a verifiable goal with tasks",
            title=Param("string", required=True),
            goal_description=Param("string", required=True, description="What this goal achieves"),
            tasks=Param("array", required=True,
                description="Task list. Each item is a string (simple) or "
                "{\"description\": \"...\", \"depends_on\": [\"task-index-0\", ...]} "
                "where depends_on references other tasks by their 0-based index in this array."),
            verification_script=Param("string",
                description="Bash command (exit 0 = pass). Recommended for concrete checks."),
            verification_conditions=Param("array", items={"type": "string"},
                description="Conditions for LLM evaluation. Use for subjective/complex checks. "
                "Can be combined with verification_script — script runs first."))
    async def create(self, ctx: dict[str, Any], title: str, goal_description: str, tasks: Sequence[Any],
                     verification_script: str = "",
                     verification_conditions: Sequence[Any] | None = None) -> str:
        if not verification_script and not verification_conditions:
            return "Error: at least one of verification_script or verification_conditions is required"

        if verification_script:
            method = VerificationMethod.SCRIPT
        else:
            method = VerificationMethod.CONDITIONS

        verification = Verification(
            method=method,
            script=verification_script,
            conditions=tuple(verification_conditions or ()),
        )

        # Parse tasks — accept both flat strings and objects with depends_on.
        # Dependencies reference tasks by 0-based index in this array, which
        # are resolved to real task IDs after creation.
        task_descriptions, task_dep_indices = _parse_task_input(tasks)

        goal = _tracker(ctx).create_goal(
            ctx["group_id"], title, goal_description, task_descriptions,
            verification=verification,
        )

        # Resolve index-based depends_on to real task IDs
        if any(task_dep_indices):
            _apply_task_dependencies(ctx, goal, task_dep_indices)
            goal = _tracker(ctx).get_goal(goal.id)  # re-read with deps applied
        # Init workspace and queue initial planning
        manager = ctx.get("_goal_manager")
        if manager:
            manager.workspace.init_workspace(goal)
            manager.on_goal_created(goal.id)
        return (
            f"Goal created: {_format_goal(goal)}\n\n"
            "The goal system will now plan tasks, start them, run "
            "verification, and complete the goal automatically. "
            "You will receive task instructions — wait for them. "
            "Do NOT work on the tasks yourself until instructed."
        )

    @action("list", description="List goals with progress",
            status=Param("string", default="active", enum=["active", "completed", "abandoned", "all"]))
    async def list(self, ctx: dict[str, Any], status: str = "active") -> str:
        goals = _tracker(ctx).list_goals(status_filter=status)
        if not goals:
            return f"No {status} goals found."
        parts: list[str] = []
        for goal in goals:
            done = sum(1 for t in goal.tasks if t.status == TaskStatus.COMPLETED)
            parts.append(f"{_format_goal(goal)} ({done}/{len(goal.tasks)} done)")
        return "\n\n".join(parts)

    @action("update_task", description="Mark a task as complete or skip it",
            goal_id=Param("string", required=True),
            task_id=Param("string", required=True),
            task_status=Param("string", required=True, enum=["complete", "skip"],
                description="complete (task done) or skip (task not needed)"),
            summary=Param("string", description="Required when task_status=complete"),
            notes=Param("string"))
    async def update_task(self, ctx: dict[str, Any], goal_id: str, task_id: str, task_status: str,
                          summary: str = "", notes: str = "") -> str:
        resolved = _TASK_STATUS_MAP.get(task_status)
        if resolved is None:
            return f"Error: task_status must be one of: {', '.join(_TASK_STATUS_MAP)}"
        if task_status == "complete" and not summary:
            return "Error: summary is required when completing a task"
        _tracker(ctx).update_task(
            goal_id, task_id, resolved, summary=summary, notes=notes,
        )
        # State machine handles transitions (may start next task, queue verification)
        manager = ctx.get("_goal_manager")
        if manager:
            manager.on_task_updated(goal_id, task_id, resolved)
        # Re-read goal AFTER state machine ran — it may have started the next task
        goal = _tracker(ctx).get_goal(goal_id)
        result = f"update_task ({task_status}): {_format_goal(goal)}"

        # Check if all tasks are done — remind the agent to stop
        pending = [t for t in goal.tasks if t.status in ("pending", "in_progress")]
        if not pending:
            result += (
                "\n\nAll tasks are done. The system will now run verification "
                "and complete the goal automatically. Do NOT run verification "
                "or complete the goal yourself."
            )
        return result

    @action("add_task", description="Add a task to a goal (used by planner)",
            goal_id=Param("string", required=True),
            task_description=Param("string", required=True, description="Task description"),
            depends_on=Param("array", items={"type": "string"},
                description="Task IDs that must complete before this task can start"))
    async def add_task(self, ctx: dict[str, Any], goal_id: str, task_description: str,
                       depends_on: Sequence[Any] | None = None) -> str:
        goal = _tracker(ctx).add_task(
            goal_id, task_description,
            depends_on=tuple(depends_on or ()),
        )
        manager = ctx.get("_goal_manager")
        if manager:
            manager.on_task_added(goal_id)
        return f"add_task: {_format_goal(goal)}"

    @action("abandon_goal", description="Abandon a goal",
            goal_id=Param("string", required=True), summary=Param("string"))
    async def abandon_goal(self, ctx: dict[str, Any], goal_id: str, summary: str = "") -> str:
        goal = _tracker(ctx).update_goal_status(
            goal_id, GoalStatus.ABANDONED, summary=summary,
        )
        # Notify for TUI and clean up workspace
        manager = ctx.get("_goal_manager")
        if manager:
            manager.tracker.add_notification(
                goal_id, goal.title, summary or "Abandoned", "abandoned",
            )
            manager.workspace.remove_workspace(goal_id)
        return f"abandon_goal: {_format_goal(goal)}"


def create_goal_tool():
    return GoalTool().build()
