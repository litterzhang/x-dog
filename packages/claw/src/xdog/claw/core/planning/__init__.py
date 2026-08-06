"""Planning — goals (what to do) and schedules (when to do it).

Both domains own their tools and register them with the tool registry.
"""
from xdog.claw.core.planning.goal_tracker import GoalTracker
from xdog.claw.core.planning.task_scheduler import TaskScheduler
from xdog.claw.core.tools.registry import register
from xdog.claw.core.tools.tool_goal import create_goal_tool
from xdog.claw.core.tools.tool_task import create_task_tool

register("goal", create_goal_tool)
register("task", create_task_tool)

__all__ = ["GoalTracker", "TaskScheduler"]
