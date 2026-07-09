"""Skills — procedural memory for the agent.

The skills domain owns its tool and registers it with the tool registry.
"""
from claw.core.skills.skill_manager import SkillManager  # noqa: F401
from claw.core.tools.registry import register
from claw.core.tools.tool_skill import create_skill_tool

register("skill", create_skill_tool)
