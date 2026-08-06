"""Skill types — frozen dataclass for skill metadata and content."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    """A reusable skill (procedural memory).

    Skills are markdown files that encode workflows, procedures, and
    learned approaches. The agent creates them after completing complex
    tasks and loads them when similar work arises.
    """

    name: str = ""
    slug: str = ""
    description: str = ""
    content: str = ""
    created: str = ""
    updated: str = ""
    path: Path | None = None
    #: True when the skill came from an installed package rather than a user
    #: directory. Packaged skills are read-only — they live in site-packages,
    #: and editing or deleting one would corrupt the installation.
    packaged: bool = False
    #: How long the skill stays active once invoked: ``"session"`` (default,
    #: until unloaded) or ``"turn"`` (one turn, then it retires itself).
    #:
    #: Declared by the author under the standard's ``metadata`` field, because
    #: only the author knows which kind of skill this is. A one-shot procedure
    #: should get out of the way; a guardrail — "always run the tests before
    #: deploying" — must not, and neither a timer nor the model it constrains
    #: is in a position to tell those apart.
    scope: str = "session"

    @property
    def expires_after_turn(self) -> bool:
        return self.scope == "turn"

    @property
    def directory(self) -> Path | None:
        """The skill's own directory — what its relative paths resolve against."""
        return self.path.parent if self.path is not None else None
