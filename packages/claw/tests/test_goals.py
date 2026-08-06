"""Tests for GoalTracker."""

import pytest
from xdog.claw.core.planning.goal_tracker import GoalTracker
from xdog.claw.core.types import GoalStatus, TaskStatus


@pytest.fixture
def tracker(tmp_path):
    return GoalTracker(tmp_path / "goals.json")

def test_update_task_status(tracker):
    goal = tracker.create_goal("g1", "G", "D", ["a", "b"])
    updated = tracker.update_task(goal.id, goal.tasks[0].id, TaskStatus.COMPLETED, summary="did it")
    assert updated.tasks[0].status == TaskStatus.COMPLETED
    assert updated.tasks[0].summary == "did it"
    assert updated.tasks[1].status == TaskStatus.PENDING

def test_complete_and_abandon_goal(tracker):
    goal = tracker.create_goal("g1", "G", "D", ["a"])
    completed = tracker.update_goal_status(goal.id, GoalStatus.COMPLETED, summary="all done")
    assert completed.status == GoalStatus.COMPLETED
    assert completed.summary == "all done"

    goal2 = tracker.create_goal("g1", "G2", "D", ["a"])
    abandoned = tracker.update_goal_status(goal2.id, GoalStatus.ABANDONED)
    assert abandoned.status == GoalStatus.ABANDONED

def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "goals.json"
    t1 = GoalTracker(path)
    goal = t1.create_goal("g1", "Persist", "D", ["a"])
    t1.update_task(goal.id, goal.tasks[0].id, TaskStatus.COMPLETED, summary="did it")

    t2 = GoalTracker(path)
    loaded = t2.get_goal(goal.id)
    assert loaded.tasks[0].status == TaskStatus.COMPLETED
