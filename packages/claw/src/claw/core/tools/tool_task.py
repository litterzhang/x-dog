"""Task scheduling tool — schedule, cancel, list. Uses ToolDef framework."""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path as _Path
from agent.tool_def import ToolDef, Param, action


def _tasks_file(ctx: dict) -> _Path:
    """Resolve tasks file path from ctx, falling back to config."""
    tasks_file = ctx.get("tasks_file")
    if tasks_file:
        return _Path(tasks_file)
    data_dir = ctx.get("data_dir")
    if data_dir:
        return _Path(data_dir) / "scheduled_tasks.json"
    # Last resort: read from config (backward compat)
    from claw.config import load_config
    return _Path(load_config().tasks_file)


def _load_tasks(ctx: dict) -> list[dict]:
    tf = _tasks_file(ctx)
    if not tf.exists():
        return []
    try:
        data = json.loads(tf.read_text(encoding="utf-8"))
        return data.get("tasks", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_tasks(ctx: dict, tasks: list[dict]) -> None:
    tf = _tasks_file(ctx)
    tf.parent.mkdir(parents=True, exist_ok=True)
    tf.write_text(json.dumps({"tasks": tasks}, indent=2) + "\n", encoding="utf-8")


class TaskTool(ToolDef):
    name = "task"
    description = "Manage scheduled tasks: schedule, cancel, or list."

    @action("schedule", description="Schedule a recurring task",
            group_id=Param("string", description="Target group"),
            cron=Param("string", required=True, description="Cron expression"),
            prompt=Param("string", required=True, description="What to do"))
    async def schedule(self, ctx, cron: str, prompt: str, group_id: str = ""):
        tasks = _load_tasks(ctx)
        task = {
            "id": f"task-{uuid.uuid4().hex[:8]}",
            "group_id": group_id,
            "prompt": prompt,
            "schedule": {"cron": cron},
            "enabled": True,
            "last_run": None,
            "created_at": time.time(),
        }
        tasks.append(task)
        _save_tasks(ctx, tasks)
        return f"Task scheduled: {task['id']}"

    @action("cancel", description="Cancel a task by ID",
            task_id=Param("string", required=True, description="Task ID to cancel"))
    async def cancel(self, ctx, task_id: str):
        tasks = _load_tasks(ctx)
        before = len(tasks)
        tasks = [t for t in tasks if t.get("id") != task_id]
        if len(tasks) == before:
            return f"No task found with id {task_id}."
        _save_tasks(ctx, tasks)
        return f"Task {task_id} cancelled."

    @action("list", description="List all scheduled tasks")
    async def list(self, ctx):
        tasks = _load_tasks(ctx)
        if not tasks:
            return "No scheduled tasks."
        lines = []
        for t in tasks:
            status = "enabled" if t.get("enabled", True) else "disabled"
            schedule = t.get("schedule", {})
            sched_desc = (
                schedule.get("cron")
                or (f"every {schedule.get('interval_seconds')}s"
                    if schedule.get("interval_seconds") else None)
                or "one-off"
            )
            lines.append(
                f"- **{t['id']}** [{status}] group={t.get('group_id', '?')} "
                f"schedule={sched_desc} prompt=\"{t.get('prompt', '')}\""
            )
        return "\n".join(lines)


def create_task_tool():
    return TaskTool().build()
