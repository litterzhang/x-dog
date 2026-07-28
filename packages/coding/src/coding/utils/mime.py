"""MIME type detection utility."""

from __future__ import annotations

import mimetypes
from pathlib import Path

# Extra mappings beyond the stdlib defaults
_EXTRA_TYPES: dict[str, str] = {
    ".ts": "text/typescript",
    ".tsx": "text/typescript-jsx",
    ".jsx": "text/javascript-jsx",
    ".md": "text/markdown",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/toml",
    ".rs": "text/rust",
    ".go": "text/x-go",
    ".svelte": "text/svelte",
    ".vue": "text/vue",
    ".astro": "text/astro",
    ".ipynb": "application/x-ipynb+json",
}


def _init() -> None:
    """Register extra MIME types."""
    for ext, mime in _EXTRA_TYPES.items():
        mimetypes.add_type(mime, ext)


_init()


def detect_mime(path: str | Path) -> str:
    """Detect the MIME type of a file by extension.

    Returns ``"application/octet-stream"`` if the type cannot be determined.
    """
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def is_text(path: str | Path) -> bool:
    """Heuristic: is this file likely a text file?"""
    mime = detect_mime(path)
    return mime.startswith("text/") or mime in {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-ipynb+json",
        "application/toml",
        "application/yaml",
    }


def is_image(path: str | Path) -> bool:
    """Check if the file is an image based on MIME type."""
    mime = detect_mime(path)
    return mime.startswith("image/")
