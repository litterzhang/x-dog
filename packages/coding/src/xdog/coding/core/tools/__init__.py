"""Tool setup for the coding agent.

Uses the agent package's built-in tool factories to create the standard
tool set. No custom tool classes needed -- everything is an ``AgentTool``.
"""

from __future__ import annotations

from pathlib import Path

from xdog.agent import AgentTool
from xdog.agent.tools import (
    create_bash_tool,
    create_current_time_tool,
    create_filesystem_tool,
)


def get_default_tools(working_dir: Path) -> list[AgentTool]:
    """Create the standard tool set for a coding agent session.

    Parameters
    ----------
    working_dir:
        The initial working directory for the bash tool.

    Returns
    -------
    list[AgentTool]
        Ready-to-use tools backed by the agent package's built-in factories.
    """
    return [
        create_bash_tool(initial_cwd=working_dir),
        create_filesystem_tool(),
        create_current_time_tool(),
    ]
