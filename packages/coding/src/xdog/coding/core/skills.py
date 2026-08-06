"""Skills system: loadable skill definitions (slash commands)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from xdog.coding.config import get_skills_dir


@dataclass(frozen=True)
class Skill:
    """A single loadable skill (slash command)."""

    name: str
    description: str
    prompt_template: str
    aliases: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)

    def render(self, **kwargs: Any) -> str:
        """Render the prompt template with the given parameters."""
        try:
            return self.prompt_template.format(**kwargs)
        except KeyError:
            return self.prompt_template


class SkillRegistry:
    """Registry of available skills, loaded from YAML files on disk."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir or get_skills_dir()
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._skills_dir.is_dir():
            return
        for path in self._skills_dir.glob("*.yaml"):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                skill = Skill(
                    name=raw.get("name", path.stem),
                    description=raw.get("description", ""),
                    prompt_template=raw.get("prompt", ""),
                    aliases=tuple(raw.get("aliases", [])),
                    parameters=raw.get("parameters", {}),
                )
                self._skills[skill.name] = skill
                for alias in skill.aliases:
                    self._skills[alias] = skill
            except Exception:
                continue

    def get(self, name: str) -> Skill | None:
        """Look up a skill by name or alias."""
        self._ensure_loaded()
        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        """Return all unique skills (de-duplicated from aliases)."""
        self._ensure_loaded()
        seen: set[str] = set()
        result: list[Skill] = []
        for skill in self._skills.values():
            if skill.name not in seen:
                seen.add(skill.name)
                result.append(skill)
        return sorted(result, key=lambda s: s.name)
