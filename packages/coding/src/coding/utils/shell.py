"""Shell utilities: environment detection, shell quoting, etc."""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShellInfo:
    """Information about the user's shell environment."""

    shell_path: str
    shell_name: str
    term: str
    has_color: bool

    @classmethod
    def detect(cls) -> ShellInfo:
        shell = os.environ.get("SHELL", "/bin/bash")
        term = os.environ.get("TERM", "")
        return cls(
            shell_path=shell,
            shell_name=Path(shell).name,
            term=term,
            has_color=_supports_color(term),
        )


def _supports_color(term: str) -> bool:
    """Heuristic check for color support."""
    if not term:
        return False
    color_terms = {"xterm", "xterm-256color", "screen", "screen-256color", "tmux", "tmux-256color"}
    return term in color_terms or "color" in term.lower()


def shell_quote(s: str) -> str:
    """Safely quote a string for shell use."""
    return shlex.quote(s)


def which(program: str) -> str | None:
    """Find a program on the PATH, like ``which``."""
    result = shutil.which(program)
    return result


def is_program_available(program: str) -> bool:
    """Check if a program is available on the PATH."""
    return which(program) is not None


def get_editor() -> str:
    """Return the user's preferred editor."""
    return (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or "vi"
    )


def expand_env_vars(text: str) -> str:
    """Expand environment variables in *text*."""
    return os.path.expandvars(text)


def get_terminal_size() -> tuple[int, int]:
    """Return (columns, lines) of the terminal."""
    try:
        size = os.get_terminal_size()
        return (size.columns, size.lines)
    except OSError:
        return (80, 24)
