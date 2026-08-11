"""Skills — reusable procedural knowledge an agent can discover and load.

A skill is a directory holding a ``SKILL.md``: YAML frontmatter (``name``,
``description``) followed by a markdown body. The frontmatter is cheap enough to
keep in the system prompt for every skill on disk; the body is only read when the
agent decides it needs that skill. That two-step disclosure is the whole point —
a hundred skills cost a hundred one-line descriptions, not a hundred documents.

This module deliberately depends on nothing but the standard library, because it
is shared: ``xdog.claw`` lets an agent *author* skills as procedural memory,
``xdog.coding`` reads them to answer slash commands, and packages can ship their
own — ``xdog.flow`` carries one at ``xdog/flow/skills/`` teaching an agent to
write flow workflows.
"""

from xdog.agent.skills.discovery import load_packaged_skill, packaged_skills
from xdog.agent.skills.manager import SkillManager
from xdog.agent.skills.render import render_skill_body, skills_preamble
from xdog.agent.skills.types import Skill

__all__ = [
    "Skill",
    "SkillManager",
    "load_packaged_skill",
    "packaged_skills",
    "render_skill_body",
    "skills_preamble",
]
