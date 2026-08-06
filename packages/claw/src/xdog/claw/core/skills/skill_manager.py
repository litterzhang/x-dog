"""Skill manager — load, save, list, patch, remove skills.

Skills have two tiers:
- **Shared** — ``{data_dir}/skills/`` — available to all groups
- **Group** — ``{workspace}/skills/`` — specific to one group

When listing or loading, both directories are searched. Group skills
take precedence when slugs conflict. New skills are saved to the
shared directory by default (so other groups benefit). Use
``scope="group"`` to save a group-specific skill.
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import date
from pathlib import Path

from xdog.claw.core.skills.types import Skill

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Convert a name to a filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9\s-]", "", name.lower())
    slug = re.sub(r"[\s]+", "-", slug.strip())
    return slug or "untitled"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter from markdown body."""
    if not text.startswith("---"):
        return {}, text

    end = text.find("---", 3)
    if end == -1:
        return {}, text

    fm_text = text[3:end].strip()
    body = text[end + 3:].strip()

    meta: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    return meta, body


def _build_frontmatter(name: str, description: str, created: str, updated: str) -> str:
    """Build YAML frontmatter string."""
    lines = ["---"]
    lines.append(f"name: {name}")
    if description:
        lines.append(f"description: {description}")
    lines.append(f"created: {created}")
    lines.append(f"updated: {updated}")
    lines.append("---")
    return "\n".join(lines)


def _load_skill_from_dir(skill_dir: Path) -> Skill | None:
    """Load a skill from a directory."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None

    text = skill_file.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)

    desc = meta.get("description", "")
    if not desc:
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                desc = line[:120]
                break

    return Skill(
        name=meta.get("name", skill_dir.name),
        slug=skill_dir.name,
        description=desc,
        content=body,
        created=meta.get("created", ""),
        updated=meta.get("updated", ""),
        path=skill_file,
    )


class SkillManager:
    """Manages reusable skills (procedural memory) across two tiers.

    - ``shared_dir``: skills available to all groups
    - ``group_dir``: skills specific to one group (takes precedence)
    """

    def __init__(
        self,
        shared_dir: Path,
        group_dir: Path | None = None,
    ) -> None:
        self._shared_dir = shared_dir
        self._shared_dir.mkdir(parents=True, exist_ok=True)
        self._group_dir = group_dir
        if self._group_dir:
            self._group_dir.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> list[Skill]:
        """List all skills. Group skills override shared on slug conflict."""
        by_slug: dict[str, Skill] = {}

        # Load shared first
        for skill in self._list_from_dir(self._shared_dir):
            by_slug[skill.slug] = skill

        # Group overrides shared
        if self._group_dir:
            for skill in self._list_from_dir(self._group_dir):
                by_slug[skill.slug] = skill

        return sorted(by_slug.values(), key=lambda s: s.slug)

    def load_skill(self, slug: str) -> Skill | None:
        """Load full skill content. Group takes precedence over shared."""
        if self._group_dir:
            skill = _load_skill_from_dir(self._group_dir / slug)
            if skill:
                return skill
        return _load_skill_from_dir(self._shared_dir / slug)

    def save_skill(
        self,
        slug: str,
        content: str,
        *,
        name: str = "",
        description: str = "",
        scope: str = "shared",
    ) -> Skill:
        """Create or overwrite a skill.

        ``scope`` is ``"shared"`` (default, all groups) or ``"group"``
        (this group only).
        """
        if not slug:
            slug = _slugify(name or "untitled")

        if scope == "group" and self._group_dir:
            target_dir = self._group_dir
        else:
            target_dir = self._shared_dir

        skill_dir = target_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"

        today = date.today().isoformat()

        # Preserve created date if updating
        created = today
        if skill_file.exists():
            old_text = skill_file.read_text(encoding="utf-8")
            old_meta, _ = _parse_frontmatter(old_text)
            created = old_meta.get("created", today)

        fm = _build_frontmatter(
            name=name or slug,
            description=description,
            created=created,
            updated=today,
        )

        full_text = f"{fm}\n\n{content}\n"
        skill_file.write_text(full_text, encoding="utf-8")

        logger.info("Saved skill: %s (%s, scope=%s)", name or slug, skill_file, scope)

        return Skill(
            name=name or slug,
            slug=slug,
            description=description,
            content=content,
            created=created,
            updated=today,
            path=skill_file,
        )

    def patch_skill(self, slug: str, patch_text: str) -> Skill | None:
        """Append content to an existing skill (token-efficient)."""
        skill = self.load_skill(slug)
        if skill is None:
            return None

        return self.save_skill(
            slug,
            skill.content + "\n\n" + patch_text,
            name=skill.name,
            description=skill.description,
        )

    def remove_skill(self, slug: str) -> bool:
        """Delete a skill from both tiers."""
        removed = False
        for d in [self._group_dir, self._shared_dir]:
            if d and (d / slug).exists():
                shutil.rmtree(d / slug)
                removed = True
                logger.info("Removed skill: %s from %s", slug, d)
        return removed

    def skills_summary(self) -> str:
        """One-line descriptions for progressive disclosure in the prompt."""
        skills = self.list_skills()
        if not skills:
            return ""
        lines = ["# Available Skills", ""]
        for s in skills:
            desc = f" — {s.description}" if s.description else ""
            lines.append(f"- `{s.slug}`{desc}")
        lines.append("")
        lines.append(
            "Use the skill tool (action: load, slug: \"...\") to load "
            "the full content of a skill when you need it."
        )
        return "\n".join(lines)

    def _list_from_dir(self, skills_dir: Path) -> list[Skill]:
        """List skills from a single directory (metadata only)."""
        skills: list[Skill] = []
        if not skills_dir.exists():
            return skills

        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill = _load_skill_from_dir(skill_dir)
            if skill:
                # Strip full content for listing (progressive disclosure)
                skills.append(Skill(
                    name=skill.name,
                    slug=skill.slug,
                    description=skill.description,
                    created=skill.created,
                    updated=skill.updated,
                    path=skill.path,
                ))

        return skills
