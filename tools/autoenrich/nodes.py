"""flow script-node entry points for the x-dog auto-enrich workflow.

Two thin, pure-Python nodes adapted to the flow script-node signature
``f(ctx, **ports) -> value``.  The workflow (``enrich.json``) references them as
``run: "nodes:scope"`` / ``run: "nodes:gate"``; this file sits next to the
workflow JSON, so the workflow's own directory is on ``sys.path`` at run time.

Neither node mutates git history — the deterministic commit/revert is the
driver's job.  ``scope`` inspects the working tree the BUILD agent edited and
reverts out-of-scope *tracked* edits (recoverable from git); ``gate`` only runs
read-only checks.  Crucially ``scope`` NEVER deletes untracked files — it only
excludes out-of-scope ones from the reported diff — so it can never destroy
un-committed work elsewhere in the tree (including this scaffold itself).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _changed_paths(repo: str) -> list[str]:
    """Tracked-file changes (porcelain, path column starts at index 3)."""
    out = _run(["git", "-C", repo, "status", "--porcelain"], repo, 60).stdout
    return [ln[3:].strip() for ln in out.splitlines() if ln.strip()]


def _untracked(repo: str) -> list[str]:
    out = _run(["git", "-C", repo, "ls-files", "--others", "--exclude-standard"], repo, 60).stdout
    return out.split()


def scope(ctx: Any, repo: str, allow_paths: str) -> str:
    """Collect the BUILD agent's in-scope diff. READ-ONLY — reverts NOTHING.

    ``allow_paths`` is a whitespace/comma-separated list of path prefixes the task
    is permitted to touch (e.g. ``packages/flow/``).  This node only *inspects* the
    working tree: it reports the diff and changed paths restricted to the
    allow-list, and simply IGNORES anything out of scope.  It never runs
    ``git checkout`` / ``git clean`` and never deletes files, so it can never
    destroy un-committed work — including edits to this scaffold itself or the
    caller's own unrelated changes.  (``uv run`` rewrites ``uv.lock`` as a
    side-effect; restricting to the allow-list keeps that noise out of the result.)

    Enforcement of "only touch allowed paths" is delegated to the driver, which
    commits ONLY the allow-listed paths — so out-of-scope edits never get staged.

    Returns a JSON string ``{allow, changed, summary, diff}`` covering in-scope
    paths only.
    """
    repo_p = Path(repo)
    allow = [p for p in allow_paths.replace(",", " ").split() if p]

    def _ok(p: str) -> bool:
        return any(p == a or p.startswith(a) for a in allow)

    # Assemble the in-scope diff (tracked edits + new-file bodies), allow-list only.
    # No mutation of the working tree happens here.
    diff = _run(["git", "-C", repo, "diff", "--", *allow], repo, 120).stdout if allow else ""
    for p in _untracked(repo):
        if _ok(p) and (repo_p / p).is_file():
            try:
                body = (repo_p / p).read_text(encoding="utf-8", errors="replace")
                diff += f"\n+++ b/{p}\n" + "".join(f"+{ln}\n" for ln in body.splitlines())
            except OSError:
                pass

    changed = [p for p in _changed_paths(repo) if _ok(p)] + [p for p in _untracked(repo) if _ok(p)]
    summary = f"{len(changed)} in-scope path(s): " + ", ".join(changed[:8])
    return json.dumps({"allow": allow, "changed": changed, "summary": summary, "diff": diff}, ensure_ascii=False)


def gate(ctx: Any, repo: str, scope: str) -> str:
    """Deterministic gate: ruff -> mypy --strict -> pytest on packages/flow.

    Runs the same three checks a human would before committing a flow change.
    Returns ``PASS`` when all three pass, else ``FAIL: <stage> — <tail>``.  A no-op
    change (nothing in scope) is a FAIL — the task must actually change code.
    """
    scope_data = json.loads(scope) if scope else {}
    if not scope_data.get("changed"):
        return "FAIL: no in-scope changes — the BUILD agent produced an empty (or out-of-scope) diff"

    stages = (
        ("ruff", ["uv", "run", "ruff", "check", "packages/flow"]),
        ("mypy", ["uv", "run", "mypy", "--strict", "packages/flow/src"]),
        ("pytest", ["uv", "run", "pytest", "packages/flow/tests", "-q"]),
    )
    for name, cmd in stages:
        proc = _run(cmd, repo, 900)
        if proc.returncode != 0:
            tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().splitlines()
            return f"FAIL: {name} — " + " | ".join(tail[-8:])
    return "PASS"
