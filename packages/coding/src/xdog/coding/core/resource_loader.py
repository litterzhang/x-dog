"""Resource loader: discover and load project context files and bundled assets.

Scans for project-level context files (CLAUDE.md, AGENTS.md, .coding/)
and loads bundled prompt fragments from the package resources directory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Context file names to discover (in priority order)
CONTEXT_FILE_NAMES = (
    "CLAUDE.md",
    "AGENTS.md",
    ".coding/INSTRUCTIONS.md",
    ".coding/instructions.md",
)

# Max file size for context files (64KB)
MAX_CONTEXT_FILE_SIZE = 64 * 1024


@dataclass(frozen=True)
class ContextFile:
    """A discovered project context file."""

    path: Path
    content: str
    relative_path: str


@dataclass
class ProjectResources:
    """All discovered resources for a project."""

    context_files: list[ContextFile] = field(default_factory=list)
    custom_instructions: str = ""

    def to_file_entries(self) -> list[dict[str, Any]]:
        """Convert context files to file entry dicts for the system prompt."""
        entries: list[dict[str, Any]] = []
        for cf in self.context_files:
            entries.append({
                "type": "file_content",
                "path": cf.relative_path,
                "content": cf.content,
            })
        return entries


def get_package_resource_dir() -> Path:
    """Return the path to the package's bundled resource directory.

    Falls back to the package source directory itself if no dedicated
    ``resources`` sub-directory exists.
    """
    pkg_dir = Path(__file__).resolve().parent
    res_dir = pkg_dir / "resources"
    return res_dir if res_dir.is_dir() else pkg_dir


def load_text_resource(name: str) -> str:
    """Load a text file from the resource directory.

    Returns an empty string if the file does not exist.
    """
    path = get_package_resource_dir() / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def load_prompt_fragment(name: str) -> str:
    """Load a prompt fragment by name (e.g. ``"tools_header.txt"``)."""
    return load_text_resource(f"prompts/{name}")


def discover_context_files(working_dir: Path) -> list[ContextFile]:
    """Discover project context files in the working directory.

    Scans for well-known files like CLAUDE.md, AGENTS.md, and files
    inside the .coding/ project directory.
    """
    found: list[ContextFile] = []

    for name in CONTEXT_FILE_NAMES:
        path = working_dir / name
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_CONTEXT_FILE_SIZE:
                logger.warning("Skipping large context file: %s (%d bytes)", path, path.stat().st_size)
                continue
            content = path.read_text(encoding="utf-8")
            found.append(ContextFile(
                path=path,
                content=content,
                relative_path=name,
            ))
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Failed to read context file %s: %s", path, exc)

    return found


def load_project_resources(working_dir: Path) -> ProjectResources:
    """Load all project resources from the working directory.

    Discovers context files, custom instructions, and other
    project-level configuration.
    """
    context_files = discover_context_files(working_dir)

    # Extract custom instructions from .coding/instructions files
    custom_parts: list[str] = []
    for cf in context_files:
        if "instructions" in cf.relative_path.lower():
            custom_parts.append(cf.content)

    return ProjectResources(
        context_files=context_files,
        custom_instructions="\n\n".join(custom_parts),
    )


def reload_resources(working_dir: Path) -> ProjectResources:
    """Re-scan and reload project resources.

    Alias for load_project_resources, but makes the intent explicit
    for callers that need to refresh on demand.
    """
    return load_project_resources(working_dir)
