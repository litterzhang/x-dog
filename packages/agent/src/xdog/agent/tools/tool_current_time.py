"""current_time tool — returns the current date, time, and timezone."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from xdog.agent.core import AgentTool
from xdog.agent.tool_def import ToolDef


class CurrentTimeTool(ToolDef):
    name = "current_time"
    description = "Returns the current date, time, and timezone."

    async def execute(self, ctx: dict[str, Any]) -> str:
        now = datetime.now(timezone.utc).astimezone()
        return now.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")


def create_current_time_tool() -> AgentTool:
    return CurrentTimeTool().build()
