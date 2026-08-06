"""Making a skill's file references resolvable.

A skill may ship files beside its ``SKILL.md`` — the standard names
``scripts/``, ``references/`` and ``assets/`` as conventions — and says to
reference them by paths relative to the skill root. That instruction is only
followable if the reader knows where the root is, and a model reading the body
does not: it sees ``examples/essay_writer.json`` and resolves it against the
working directory, where nothing of the sort exists.

Our own shipped skill had exactly this bug. It tells the agent to "read the
closest example and copy its shape" and ships twelve of them, and every one of
those paths was unresolvable — a failure that produces a confused agent rather
than an error, which is why it survived being written, reviewed and packaged.

Two halves to the fix. Explicit ``${SKILL_DIR}`` markers are substituted, and
the directory is stated up front so that bare relative paths — the form the
standard actually recommends — resolve too, with no change to the skill.
"""
from __future__ import annotations

from xdog.agent.skills.types import Skill

#: Substituted with the skill's directory. ``CLAUDE_SKILL_DIR`` is the spelling
#: Claude Code uses; honouring it means a skill written for that client works
#: here unmodified, which is most of what portability is worth in practice.
SKILL_DIR_VARIABLES = ("${SKILL_DIR}", "${CLAUDE_SKILL_DIR}")


def render_skill_body(skill: Skill) -> str:
    """The skill's instructions, with its file references made resolvable."""
    directory = skill.directory
    if directory is None:
        return skill.content

    body = skill.content
    for variable in SKILL_DIR_VARIABLES:
        body = body.replace(variable, str(directory))

    return (
        f"This skill's files are in `{directory}`. "
        "Paths it mentions relative to that directory — `examples/x.json`, "
        "`scripts/y.sh` — resolve there, not against the working directory.\n\n"
        f"{body}"
    )
