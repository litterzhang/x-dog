"""Shared utilities for built-in tools."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Sequence
from pathlib import Path

_MAX_OUTPUT_CHARS = 30_000
_BASH_TEMP_THRESHOLD = 30_000
_MAX_LINE_LENGTH = 2000
_DEFAULT_READ_LIMIT = 2000
_DEFAULT_BASH_TIMEOUT_MS = 120_000
_MAX_BASH_TIMEOUT_MS = 600_000
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})
_IMAGE_MAX_SIZE = 5 * 1024 * 1024  # 5 MB

_BLOCKED_DIRS = frozenset({
    "/bin", "/sbin", "/usr/bin", "/usr/sbin",
    "/boot", "/dev", "/proc", "/sys",
    "/etc/shadow", "/etc/passwd",
})

_DEFAULT_GREP_MATCH_LIMIT = 100
_GREP_LINE_MAX_CHARS = 500
_DEFAULT_FIND_LIMIT = 1000
_DEFAULT_LS_LIMIT = 500


def validate_path(file_path: str, *, confine_to: Sequence[Path] | None = None) -> str | None:
    """Validate a file path. Returns an error message, or None if it is allowed.

    Two modes, and the difference matters:

    * Without ``confine_to`` this is a **denylist** — a handful of places nothing
      should touch. An unlisted path is allowed.
    * With ``confine_to`` it is an **allowlist**: the path must resolve inside one
      of the given roots. An unlisted path is *denied*.

    The allowlist is the one that confines. It compares resolved paths, so a
    symlink pointing out of the workspace is caught by where it lands rather than
    by how it is spelled.
    """
    if not file_path:
        return "Error: file path must not be empty."
    if not os.path.isabs(file_path):
        return "Error: file path must be absolute."
    try:
        resolved = Path(file_path).resolve()
    except (OSError, ValueError) as exc:
        return f"Error: invalid path: {exc}"
    resolved_str = str(resolved)
    parts = Path(file_path).parts
    if ".." in parts:
        return "Error: path traversal (..) is not allowed."
    for blocked in _BLOCKED_DIRS:
        if resolved_str == blocked or resolved_str.startswith(blocked + "/"):
            return f"Error: access to {blocked} is not allowed."
    if confine_to is not None:
        if not any(_is_within(resolved, root) for root in confine_to):
            allowed = ", ".join(str(r) for r in confine_to) or "<nothing>"
            return (
                f"Error: {resolved} is outside this run's workspace. "
                f"Allowed: {allowed}"
            )
    return None


def _is_within(candidate: Path, root: Path) -> bool:
    """Whether *candidate* is *root* or sits beneath it, after resolution."""
    try:
        resolved_root = root.resolve()
    except (OSError, ValueError):
        return False
    return candidate == resolved_root or resolved_root in candidate.parents


def truncate(text: str, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    """Truncate text to max_chars, appending a notice if truncated."""
    if len(text) <= max_chars:
        return text
    remaining = len(text) - max_chars
    return text[:max_chars] + f"\n\n... (truncated, {remaining:,} characters omitted)"


def human_size(size: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            if unit == "B":
                return f"{size} {unit}"
            return f"{size:.1f} {unit}"
        size //= 1024
    return f"{size:.1f} TB"


def shell_quote(s: str) -> str:
    """Wrap a string in single quotes, escaping internal single quotes."""
    return "'" + s.replace("'", "'\\''") + "'"


def try_update_cwd(command: str, cwd: Path) -> Path:
    """Attempt to track ``cd`` commands and return the new CWD."""
    stripped = command.strip()
    if stripped.startswith("cd "):
        parts = stripped[3:].split("&&", 1)
        target = parts[0].strip().strip('"').strip("'")
        if target:
            candidate = (cwd / target).resolve()
            if candidate.is_dir():
                return candidate
    return cwd


def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill a process and its entire process tree."""
    pid = proc.pid
    if pid is None:
        return
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
