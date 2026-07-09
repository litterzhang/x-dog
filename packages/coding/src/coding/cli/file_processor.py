"""Process files passed as CLI arguments into context messages."""

from __future__ import annotations

from pathlib import Path
from typing import Any


# Binary / image extensions that should be handled differently
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"})
_BINARY_EXTENSIONS = frozenset({
    ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".exe", ".dll", ".so", ".dylib",
})

MAX_FILE_SIZE = 512 * 1024  # 512 KiB


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTENSIONS


def _is_binary(path: Path) -> bool:
    return path.suffix.lower() in _BINARY_EXTENSIONS


def process_file(path: Path) -> dict[str, Any] | None:
    """Read a single file and return a context dict for the system prompt.

    Returns ``None`` when the file cannot be processed (binary, too large, etc.).
    """
    resolved = path.resolve()
    if not resolved.is_file():
        return None

    if _is_binary(resolved):
        return {
            "type": "file_reference",
            "path": str(resolved),
            "note": "Binary file - contents not included.",
        }

    if _is_image(resolved):
        return {
            "type": "image",
            "path": str(resolved),
        }

    try:
        stat = resolved.stat()
    except OSError:
        return None

    if stat.st_size > MAX_FILE_SIZE:
        return {
            "type": "file_reference",
            "path": str(resolved),
            "note": f"File too large ({stat.st_size:,} bytes) - contents not included.",
        }

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    return {
        "type": "file_content",
        "path": str(resolved),
        "content": content,
    }


def process_files(paths: tuple[Path, ...] | list[Path]) -> list[dict[str, Any]]:
    """Process multiple files and return a list of context dicts."""
    results: list[dict[str, Any]] = []
    for p in paths:
        entry = process_file(Path(p))
        if entry is not None:
            results.append(entry)
    return results
