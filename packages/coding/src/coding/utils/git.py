"""Git utilities: detect repo info, get status, diff, log, etc."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coding.core.exec_utils import run_sync


@dataclass(frozen=True)
class GitInfo:
    """Information about a git repository."""

    is_repo: bool
    root: str
    branch: str
    has_uncommitted: bool
    remote_url: str

    @classmethod
    def empty(cls) -> GitInfo:
        return cls(
            is_repo=False,
            root="",
            branch="",
            has_uncommitted=False,
            remote_url="",
        )


def detect_git_repo(directory: str | Path) -> GitInfo:
    """Detect whether *directory* is inside a git repository.

    Returns a :class:`GitInfo` with repo metadata, or an empty one
    if not a git repository.
    """
    cwd = str(directory)

    # Check if inside a git work tree
    result = run_sync("git rev-parse --is-inside-work-tree", cwd=cwd, timeout_ms=5_000)
    if not result.ok or result.stdout.strip() != "true":
        return GitInfo.empty()

    root = _git_cmd(cwd, "git rev-parse --show-toplevel")
    branch = _git_cmd(cwd, "git rev-parse --abbrev-ref HEAD")
    status = _git_cmd(cwd, "git status --porcelain")
    remote = _git_cmd(cwd, "git config --get remote.origin.url")

    return GitInfo(
        is_repo=True,
        root=root,
        branch=branch,
        has_uncommitted=bool(status.strip()),
        remote_url=remote,
    )


def git_status(directory: str | Path) -> str:
    """Return ``git status`` output."""
    return _git_cmd(str(directory), "git status")


def git_diff(directory: str | Path, *, staged: bool = False) -> str:
    """Return ``git diff`` output."""
    cmd = "git diff --staged" if staged else "git diff"
    return _git_cmd(str(directory), cmd)


def git_log(
    directory: str | Path,
    *,
    max_count: int = 10,
    oneline: bool = True,
) -> str:
    """Return recent git log entries."""
    fmt = "--oneline" if oneline else "--pretty=medium"
    cmd = f"git log {fmt} -n {max_count}"
    return _git_cmd(str(directory), cmd)


def git_current_branch(directory: str | Path) -> str:
    """Return the current branch name."""
    return _git_cmd(str(directory), "git rev-parse --abbrev-ref HEAD")


def _git_cmd(cwd: str, command: str) -> str:
    """Run a git command and return its stripped stdout."""
    result = run_sync(command, cwd=cwd, timeout_ms=10_000)
    return result.stdout.strip() if result.ok else ""
