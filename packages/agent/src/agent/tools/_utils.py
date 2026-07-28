"""Shared utilities for built-in tools."""

from __future__ import annotations

import asyncio
import os
import signal
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


def validate_path(file_path: str) -> str | None:
    """Validate a file path for security. Returns error message or None."""
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
    return None


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
