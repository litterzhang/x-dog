"""Skills — procedural memory for the agent.

``Skill`` and ``SkillManager`` now live in :mod:`xdog.agent.skills`, so that
``xdog.coding`` can read the same skill directories without depending on claw's
runtime. What stays here is the part that is genuinely claw's: the ``skill``
tool, which lets an agent write its own skills and registers with claw's tool
registry. The re-export below keeps existing callers working.
"""
from xdog.agent.skills import Skill, SkillManager  # noqa: F401 — re-export
from xdog.claw.core.tools.registry import register
from xdog.claw.core.tools.tool_skill import create_skill_tool

register("skill", create_skill_tool)
