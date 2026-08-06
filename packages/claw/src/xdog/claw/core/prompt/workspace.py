"""Workspace management — identity files, memory, bootstrap.

The workspace is the agent's brain on disk: markdown files that define
its identity, persona, user context, and memory. These files are
user-editable and override/supplement the static prompt sections.
"""
from __future__ import annotations

import logging
from pathlib import Path

from xdog.claw.core.prompt.templates import MEMORY_EMPTY, MEMORY_HEADER

logger = logging.getLogger(__name__)

# Files loaded as workspace overrides in the system prompt.
# TOOLS.md is removed — tool guidance is now generated dynamically.
WORKSPACE_FILES = ("IDENTITY.md", "AGENTS.md", "SOUL.md", "USER.md")

# Default content for workspace files.
_DEFAULT_AGENTS = "# Instructions\n\n<!-- Add task-specific instructions for your agent here. -->\n"
_DEFAULT_SOUL = "# Persona\n\n<!-- Customize the agent's personality and communication style here. -->\n"
_DEFAULT_USER = "# User\n\n<!-- Tell the agent about yourself — your name, role, preferences. -->\n"


def workspace_path(group_base: Path) -> Path:
    """Default workspace directory for a group."""
    return group_base / "workspace"


def init_workspace(
    ws: Path,
    *,
    agent_name: str = "Assistant",
    agents_content: str = "",
    soul_content: str = "",
    user_content: str = "",
) -> None:
    """Create workspace directory and identity files.

    If content strings are provided (e.g. from onboard wizard), they
    are used instead of the defaults.
    """
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "memory").mkdir(exist_ok=True)
    (ws / "conversations").mkdir(exist_ok=True)

    _write_if_missing(ws / "IDENTITY.md", f"# Identity\n\nName: {agent_name}\n")
    _write_if_missing(ws / "AGENTS.md", agents_content or _DEFAULT_AGENTS)
    _write_if_missing(ws / "SOUL.md", soul_content or _DEFAULT_SOUL)
    _write_if_missing(ws / "USER.md", user_content or _DEFAULT_USER)
    _write_if_missing(ws / "MEMORY.md", "# Long-Term Memory\n\n")


def set_identity_name(ws: Path, agent_name: str) -> None:
    """Force IDENTITY.md's ``Name:`` line to *agent_name*.

    Unlike :func:`init_workspace` (which only writes when the file is absent),
    this always applies the name — used by ``onboard`` so renaming the agent
    actually takes effect on an existing workspace. Any other IDENTITY.md content
    the user has added is preserved; only the ``Name:`` line is rewritten (or
    appended if none exists).
    """
    ws.mkdir(parents=True, exist_ok=True)
    path = ws / "IDENTITY.md"
    if not path.exists():
        path.write_text(f"# Identity\n\nName: {agent_name}\n", encoding="utf-8")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.lstrip().lower().startswith("name:"):
            lines[i] = f"Name: {agent_name}"
            replaced = True
            break
    if not replaced:
        lines += ["", f"Name: {agent_name}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_workspace_file(ws: Path, filename: str) -> str:
    """Load a single workspace file. Returns empty string if missing."""
    filepath = ws / filename
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8").strip()
        # Skip files that are just HTML comments (empty templates)
        if content and not _is_only_comments(content):
            return content
    return ""


def load_workspace_overrides(ws: Path) -> str:
    """Load all workspace override files and join them."""
    parts: list[str] = []
    for filename in WORKSPACE_FILES:
        content = load_workspace_file(ws, filename)
        if content:
            parts.append(content)
    return "\n\n".join(parts)


def load_memory_section(ws: Path) -> str:
    """Load MEMORY.md with appropriate framing."""
    filepath = ws / "MEMORY.md"
    if not filepath.exists():
        return MEMORY_EMPTY

    content = filepath.read_text(encoding="utf-8").strip()
    # Check if memory has actual content beyond the header
    lines = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return MEMORY_EMPTY

    return f"{MEMORY_HEADER}\n\n{content}"


def format_memory_section(content: str) -> str:
    """Format memory content with the appropriate header.

    Used when memory content is provided as a frozen snapshot
    instead of read from disk.
    """
    lines = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return MEMORY_EMPTY
    return f"{MEMORY_HEADER}\n\n{content}"


def run_bootstrap(ws: Path) -> str | None:
    """Read and delete BOOTSTRAP.md (one-time first-run ritual)."""
    bootstrap = ws / "BOOTSTRAP.md"
    if not bootstrap.exists():
        return None
    content = bootstrap.read_text(encoding="utf-8")
    bootstrap.unlink()
    logger.info("Bootstrap ritual completed, BOOTSTRAP.md deleted")
    return content


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _is_only_comments(text: str) -> bool:
    """Check if text contains only markdown headers and HTML comments."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("<!--") and line.endswith("-->"):
            continue
        return False
    return True
