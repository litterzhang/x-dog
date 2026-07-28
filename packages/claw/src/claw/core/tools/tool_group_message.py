"""Group message tool — send a message to another group. Uses ToolDef framework.

This is a runtime-domain tool: the runtime owns the ``send_fn`` callback
and registers this tool with the tool registry.
"""
from __future__ import annotations

from agent.tool_def import Param, ToolDef, action


class GroupMessageTool(ToolDef):
    name = "group_message"
    description = "Send a message to another agent group."
    required_ctx = ("_send_fn",)

    @action("send", description="Send a message to a group",
            group_id=Param("string", required=True, description="Target group ID"),
            text=Param("string", required=True, description="Message text"))
    async def send(self, ctx, group_id: str, text: str):
        send_fn = ctx["_send_fn"]
        await send_fn(group_id, text)
        return "Message sent."


def create_group_message_tool():
    return GroupMessageTool().build()
