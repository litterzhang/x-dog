"""Runtime spine — gateway, orchestrator, group, session.

The runtime domain owns the group_message tool and registers it here.
"""
from claw.core.tools.registry import register
from claw.core.tools.tool_group_message import create_group_message_tool

register("group_message", create_group_message_tool)
