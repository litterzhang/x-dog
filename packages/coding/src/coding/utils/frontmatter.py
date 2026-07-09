"""YAML frontmatter parser for markdown files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml


@dataclass(frozen=True)
class FrontmatterResult:
    """Result of parsing YAML frontmatter from a markdown document."""

    metadata: dict[str, Any]
    content: str
    has_frontmatter: bool


def parse_frontmatter(text: str) -> FrontmatterResult:
    """Parse YAML frontmatter from a text document.

    Frontmatter is delimited by ``---`` at the start and end, and must
    appear at the very beginning of the document.

    Example::

        ---
        title: My Document
        tags: [a, b]
        ---
        Body content here...
    """
    stripped = text.lstrip("\n")

    if not stripped.startswith("---"):
        return FrontmatterResult(metadata={}, content=text, has_frontmatter=False)

    # Find the closing ---
    rest = stripped[3:]
    end_idx = rest.find("\n---")
    if end_idx == -1:
        return FrontmatterResult(metadata={}, content=text, has_frontmatter=False)

    yaml_block = rest[:end_idx]
    body = rest[end_idx + 4:]  # skip \n---

    # Strip leading newline from body
    if body.startswith("\n"):
        body = body[1:]

    try:
        metadata = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        return FrontmatterResult(metadata={}, content=text, has_frontmatter=False)

    if not isinstance(metadata, dict):
        return FrontmatterResult(metadata={}, content=text, has_frontmatter=False)

    return FrontmatterResult(metadata=metadata, content=body, has_frontmatter=True)


def render_frontmatter(metadata: dict[str, Any], content: str) -> str:
    """Render a document with YAML frontmatter.

    Returns the complete text with ``---`` delimiters.
    """
    if not metadata:
        return content

    yaml_text = yaml.dump(metadata, default_flow_style=False).rstrip()
    return f"---\n{yaml_text}\n---\n{content}"
