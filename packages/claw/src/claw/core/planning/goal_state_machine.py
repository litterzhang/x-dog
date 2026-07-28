"""Goal state machine — deterministic goal transitions. Zero LLM cost.

Processes events (task completed, verification done, etc.) and returns
actions (start next task, run verification, request re-plan). All
transitions are pure functions — no I/O, no LLM calls, no side effects.

The GoalManager executes the returned actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from claw.core.types import Goal, GoalStatus, GoalTask, TaskStatus

# ---------------------------------------------------------------------------
# Event types (inputs to the state machine)
# ---------------------------------------------------------------------------

class GoalEventKind(StrEnum):
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_ADDED = "task_added"
    GOAL_CREATED = "goal_created"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"


@dataclass(frozen=True)
class GoalEvent:
    """An event that triggers a state transition."""
    kind: GoalEventKind
    task_id: str = ""
    detail: str = ""


# ---------------------------------------------------------------------------
# Action types (outputs from the state machine)
# ---------------------------------------------------------------------------

class GoalActionKind(StrEnum):
    START_TASK = "start_task"
    RUN_VERIFICATION = "run_verification"
    COMPLETE_GOAL = "complete_goal"
    REQUEST_PLAN = "request_plan"
    REQUEST_REPLAN = "request_replan"
    NOTIFY_AGENT = "notify_agent"
    NOOP = "noop"


@dataclass(frozen=True)
class GoalAction:
    """An action for the GoalManager to execute."""
    kind: GoalActionKind
    task_id: str = ""
    message: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

# Stall threshold: if >50% of tasks are done AND verification has failed
# this many times, request a re-plan.
_STALL_VERIFICATION_THRESHOLD = 2


class GoalStateMachine:
    """Deterministic goal state transitions.

    Pure function: takes a goal + event, returns an action. No I/O.
    The GoalManager is responsible for executing the action.

    Tracks verification failure counts per goal for stall detection.
    """

    def __init__(self) -> None:
        self._verification_fail_count: dict[str, int] = {}

    def process_event(self, goal: Goal, event: GoalEvent) -> GoalAction:
        """Determine the next action based on goal state and event.

        This is the core of the goal system. Every possible transition
        is handled here — no LLM needed.
        """
        if goal.status != GoalStatus.ACTIVE:
            return GoalAction(kind=GoalActionKind.NOOP)

        if event.kind == GoalEventKind.GOAL_CREATED:
            return GoalAction(
                kind=GoalActionKind.REQUEST_PLAN,
                reason="Goal just created — needs initial planning.",
            )

        if event.kind == GoalEventKind.TASK_COMPLETED:
            return self._on_task_completed(goal, event)

        if event.kind == GoalEventKind.TASK_FAILED:
            return self._on_task_failed(goal, event)

        if event.kind == GoalEventKind.TASK_ADDED:
            return self._start_next_pending(goal)

        if event.kind == GoalEventKind.VERIFICATION_PASSED:
            self._verification_fail_count.pop(goal.id, None)
            return GoalAction(
                kind=GoalActionKind.COMPLETE_GOAL,
                message=f"Goal verified and complete: {goal.title}",
            )

        if event.kind == GoalEventKind.VERIFICATION_FAILED:
            return self._on_verification_failed(goal, event)

        return GoalAction(kind=GoalActionKind.NOOP)

    def reset_goal(self, goal_id: str) -> None:
        """Clear tracked state for a goal (on abandon/complete)."""
        self._verification_fail_count.pop(goal_id, None)

    # -- Transition handlers -----------------------------------------------

    def _on_task_completed(self, goal: Goal, event: GoalEvent) -> GoalAction:
        """Task completed: start next ready task, or run verification if all done."""
        ready = _ready_tasks(goal)
        in_progress = _in_progress_tasks(goal)

        if not ready and not in_progress and not _pending_tasks(goal):
            # All tasks done — run verification
            return GoalAction(
                kind=GoalActionKind.RUN_VERIFICATION,
                message="All tasks completed. Running verification.",
            )

        if ready:
            # Start all ready tasks (may be multiple in parallel workstreams)
            # Return the first one — GoalManager can call process_event
            # again for additional parallel starts
            next_task = ready[0]
            return GoalAction(
                kind=GoalActionKind.START_TASK,
                task_id=next_task.id,
                message=next_task.description,
            )

        # Pending tasks exist but blocked on dependencies, or in_progress — wait
        return GoalAction(
            kind=GoalActionKind.NOOP,
            message="Waiting for in-progress tasks or blocked dependencies.",
        )

    def _on_task_failed(self, goal: Goal, event: GoalEvent) -> GoalAction:
        """Task failed/skipped: try next ready task, or re-plan if stuck.

        Re-plans immediately when no progress has been made (first task failed)
        or when no actionable tasks remain.
        """
        completed = _completed_count(goal)
        ready = _ready_tasks(goal)

        # If no tasks completed yet and one just failed, re-plan immediately.
        if completed == 0:
            return GoalAction(
                kind=GoalActionKind.REQUEST_REPLAN,
                reason=(
                    f"Task {event.task_id} failed with no tasks completed. "
                    "The current approach may be wrong."
                ),
            )

        if ready:
            next_task = ready[0]
            return GoalAction(
                kind=GoalActionKind.START_TASK,
                task_id=next_task.id,
                message=next_task.description,
            )

        in_progress = _in_progress_tasks(goal)
        if in_progress:
            return GoalAction(kind=GoalActionKind.NOOP)

        # Check if pending tasks exist but are blocked on failed dependency
        pending = _pending_tasks(goal)
        if pending:
            return GoalAction(
                kind=GoalActionKind.REQUEST_REPLAN,
                reason=(
                    f"Task {event.task_id} failed. Remaining tasks are blocked "
                    f"on dependencies that can't be met."
                ),
            )

        return GoalAction(
            kind=GoalActionKind.REQUEST_REPLAN,
            reason=f"Task {event.task_id} failed and no pending tasks remain.",
        )

    def _on_verification_failed(self, goal: Goal, event: GoalEvent) -> GoalAction:
        """Verification failed: re-plan. Detect stalls."""
        count = self._verification_fail_count.get(goal.id, 0) + 1
        self._verification_fail_count[goal.id] = count

        if _is_stalled(goal, count):
            return GoalAction(
                kind=GoalActionKind.REQUEST_REPLAN,
                reason=(
                    f"Stall detected: {_completed_count(goal)}/{len(goal.tasks)} "
                    f"tasks done but verification has failed {count} times. "
                    f"Last failure: {event.detail}"
                ),
            )

        return GoalAction(
            kind=GoalActionKind.REQUEST_REPLAN,
            reason=f"Verification failed ({count}x): {event.detail}",
        )

    def _start_next_pending(self, goal: Goal) -> GoalAction:
        """Start the first ready task (pending with all deps met), if any."""
        ready = _ready_tasks(goal)
        if ready:
            return GoalAction(
                kind=GoalActionKind.START_TASK,
                task_id=ready[0].id,
                message=ready[0].description,
            )
        return GoalAction(kind=GoalActionKind.NOOP)


# ---------------------------------------------------------------------------
# Helpers (pure functions)
# ---------------------------------------------------------------------------

def _pending_tasks(goal: Goal) -> list[GoalTask]:
    return [t for t in goal.tasks if t.status == TaskStatus.PENDING]


def _ready_tasks(goal: Goal) -> list[GoalTask]:
    """Pending tasks whose dependencies are all completed or skipped."""
    completed_ids = {
        t.id for t in goal.tasks
        if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
    }
    return [
        t for t in goal.tasks
        if t.status == TaskStatus.PENDING
        and all(dep in completed_ids for dep in t.depends_on)
    ]


def _in_progress_tasks(goal: Goal) -> list[GoalTask]:
    return [t for t in goal.tasks if t.status == TaskStatus.IN_PROGRESS]


def _completed_count(goal: Goal) -> int:
    return sum(1 for t in goal.tasks if t.status == TaskStatus.COMPLETED)


def _is_stalled(goal: Goal, verification_fail_count: int) -> bool:
    """Detect when tasks complete but goal doesn't progress."""
    if verification_fail_count < _STALL_VERIFICATION_THRESHOLD:
        return False
    completed = _completed_count(goal)
    total = len(goal.tasks)
    return total > 0 and completed > total // 2
