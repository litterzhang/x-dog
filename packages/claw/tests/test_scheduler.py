"""Tests for task scheduler."""
import time
import pytest
from claw.core.planning.task_scheduler import TaskScheduler
from claw.core.types import ScheduledTask, TaskSchedule

@pytest.fixture
def sched(tmp_path):
    return TaskScheduler(tmp_path / "tasks.json")

def test_interval_task_is_due(sched):
    task = ScheduledTask(id="t1", group_id="g1", prompt="check", schedule=TaskSchedule(interval_seconds=1))
    sched.add_task(task)
    due = sched.get_due_tasks()
    assert len(due) == 1  # Never run before, so due

def test_one_off_task_is_due(sched):
    task = ScheduledTask(id="t1", group_id="g1", prompt="once", schedule=TaskSchedule(run_at=time.time() - 10))
    sched.add_task(task)
    due = sched.get_due_tasks()
    assert len(due) == 1

def test_mark_run_disables_one_off(sched):
    task = ScheduledTask(id="t1", group_id="g1", prompt="once", schedule=TaskSchedule(run_at=time.time() - 10))
    sched.add_task(task)
    sched.mark_run("t1")
    assert not sched.get_task("t1").enabled

def test_persistence(tmp_path):
    path = tmp_path / "tasks.json"
    sched1 = TaskScheduler(path)
    sched1.add_task(ScheduledTask(id="t1", group_id="g1", prompt="persist", schedule=TaskSchedule(interval_seconds=300)))
    sched2 = TaskScheduler(path)
    assert len(sched2.list_tasks()) == 1
    assert sched2.get_task("t1").prompt == "persist"
