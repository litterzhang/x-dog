"""Path utilities: validation and security checks."""

from __future__ import annotations

import os
from pathlib import Path

# Directories that should never be written to
_BLOCKED_DIRS = frozenset({
    "/bin", "/sbin", "/usr/bin", "/usr/sbin",
    "/boot", "/dev", "/proc", "/sys",
    "/etc/shadow", "/etc/passwd",
})

# File extensions that should never be written
_BLOCKED_EXTENSIONS = frozenset({
    ".exe", ".dll", ".so", ".dylib",
})


def validate_path(file_path: str) -> str | None:
    """Validate a file path for security.

    Returns an error message string if the path is invalid, or ``None``
    if the path is acceptable.

    Checks performed:
    - Path must be non-empty.
    - Path must be absolute.
    - Path must not contain traversal components (``..``).
    - Path must not point into blocked system directories.
    """
    if not file_path:
        return "Error: file path must not be empty."

    if not os.path.isabs(file_path):
        return "Error: file path must be absolute."

    # Resolve to catch symlink-based traversals
    try:
        resolved = Path(file_path).resolve()
    except (OSError, ValueError) as exc:
        return f"Error: invalid path: {exc}"

    resolved_str = str(resolved)

    # Check for path traversal via ".." in the raw input
    parts = Path(file_path).parts
    if ".." in parts:
        return "Error: path traversal (..) is not allowed."

    # Block system directories
    for blocked in _BLOCKED_DIRS:
        if resolved_str == blocked or resolved_str.startswith(blocked + "/"):
            return f"Error: access to {blocked} is not allowed."

    return None


def validate_write_path(file_path: str) -> str | None:
    """Validate a path specifically for write operations.

    Performs all checks from :func:`validate_path` plus additional
    write-specific restrictions.
    """
    error = validate_path(file_path)
    if error:
        return error

    resolved = Path(file_path).resolve()

    # Block certain extensions
    if resolved.suffix.lower() in _BLOCKED_EXTENSIONS:
        return f"Error: writing {resolved.suffix} files is not allowed."

    return None


def is_inside_directory(path: str | Path, directory: str | Path) -> bool:
    """Check if *path* is inside *directory* (inclusive)."""
    try:
        resolved_path = Path(path).resolve()
        resolved_dir = Path(directory).resolve()
        return resolved_path == resolved_dir or str(resolved_path).startswith(str(resolved_dir) + "/")
    except (OSError, ValueError):
        return False


def normalize_path(file_path: str, base_dir: str | Path | None = None) -> str:
    """Normalize a path, resolving it relative to *base_dir* if given."""
    p = Path(file_path)
    if not p.is_absolute() and base_dir is not None:
        p = Path(base_dir) / p
    return str(p.resolve())
