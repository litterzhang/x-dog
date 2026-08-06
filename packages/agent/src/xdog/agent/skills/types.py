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
