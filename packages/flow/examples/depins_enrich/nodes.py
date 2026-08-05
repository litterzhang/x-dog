"""flow script-node entry points for the depins enrichment workflow.

These thin wrappers adapt the pure-Python ``guards.py`` / ``bootcheck.py`` (moved
verbatim from the original depins-autoenrich) to the flow script-node signature
``f(ctx, **ports) -> value``.  A flow script node references them as
``run: "nodes:scope"`` etc. (this file sits next to the workflow JSON, so the
workflow's own directory is on sys.path at run time).

Each node reads its typed input ports and returns a JSON string the next node
consumes.  Nothing here mutates git — the write side lives in ``cycle.py`` and is
reached by a conditional edge.  These only inspect the working tree the builder
agent edited.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _changed_paths(repo: str) -> list[str]:
    out = _run(["git", "-C", repo, "status", "--porcelain"], repo, 30).stdout
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        paths.append(line[3:].strip())
    return paths


def _untracked(repo: str) -> list[str]:
    return _run(["git", "-C", repo, "ls-files", "--others", "--exclude-standard"], repo, 30).stdout.split()


# Paths a CONTENT cycle may add/modify; a FEATURE cycle may touch src/depins/ + locales/.
_ALLOWED_CONTENT = (
    "src/depins/blueprints/blog.py",
    "src/depins/db_parts/projects.py",
    "src/depins/templates/projects/",
    "locales/",
    "AE_CHANGE.json",
)
_ALLOWED_FEATURE = ("src/depins/", "locales/", "AE_CHANGE.json")


def scope(ctx: Any, repo: str) -> str:
    """Read the builder's AE_CHANGE.json manifest, restore out-of-scope edits.

    Returns a JSON string ``{kind,id,summary,routes,is_feature,diff,changed}``.
    The manifest file is removed after reading so it never reaches a commit.
    """
    repo_p = Path(repo)
    manifest_path = repo_p / "AE_CHANGE.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    kind = str(manifest.get("kind", "")).strip().lower()
    if kind not in ("feature", "project", "blog"):
        kind = ""
    is_feature = kind == "feature"
    allow = _ALLOWED_FEATURE if is_feature else _ALLOWED_CONTENT

    def _ok(p: str) -> bool:
        return any(p == a or p.startswith(a) for a in allow)

    changed = _changed_paths(repo)
    untracked = _untracked(repo)
    oos_mod = [p for p in changed if not _ok(p)]
    if oos_mod:
        _run(["git", "-C", repo, "checkout", "--", *oos_mod], repo, 60)
    for p in untracked:
        if not _ok(p):
            try:
                (repo_p / p).unlink()
            except Exception:
                pass

    diff = _run(["git", "-C", repo, "diff"], repo, 60).stdout
    # include untracked new-file bodies in the diff view for guards/validator
    for p in _untracked(repo):
        if _ok(p) and (repo_p / p).is_file():
            try:
                body = (repo_p / p).read_text(encoding="utf-8", errors="replace")
                diff += f"\n+++ b/{p}\n" + "".join(f"+{ln}\n" for ln in body.splitlines())
            except Exception:
                pass

    return json.dumps(
        {
            "kind": kind,
            "id": str(manifest.get("id", "")).strip(),
            "summary": str(manifest.get("summary", "")).strip(),
            "routes": [str(r) for r in (manifest.get("routes") or []) if str(r).startswith("/")],
            "is_feature": is_feature,
            "diff": diff,
            "changed": _changed_paths(repo) + _untracked(repo),
        },
        ensure_ascii=False,
    )


def run_guards(ctx: Any, repo: str, venv_bin: str, scope: str, known_json: str) -> str:
    """Run guards.check[_feature] under the depins venv (subprocess); return JSON.

    The guards import depins' live registries (flask/babel/tinydb), so they run in
    the depins virtualenv via guards_cli.py — never in the flow process.
    """
    scope_data = json.loads(scope) if scope else {}
    known = json.loads(known_json) if known_json else {}
    job = {
        "repo": repo,
        "diff": scope_data.get("diff", ""),
        "is_feature": bool(scope_data.get("is_feature")),
        "id": scope_data.get("id", ""),
        "known": known,
    }
    here = Path(__file__).resolve().parent
    proc = subprocess.run(
        [str(Path(venv_bin) / "python"), str(here / "guards_cli.py")],
        cwd=repo,
        input=json.dumps(job),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return json.dumps({"ok": False, "reasons": [f"guards subprocess error: {proc.stderr[:600]}"]})
    # guards_cli prints a single JSON line as its last output line
    line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else "{}"
    return line


def gate(ctx: Any, repo: str, venv_bin: str, scope: str, ruff_baseline: int, stress_rounds: int) -> str:
    """Deterministic gate: ruff (new violations only) + pybabel + bootcheck sweep.

    Returns ``PASS`` or ``FAIL: <reason>`` (a string, so the workflow can branch
    on the FAIL marker exactly like the original review node).
    """
    scope_data = json.loads(scope) if scope else {}
    routes = scope_data.get("routes") or []
    if routes:
        os.environ["AE_EXTRA_ROUTES"] = " ".join(routes)
    else:
        os.environ.pop("AE_EXTRA_ROUTES", None)
    os.environ["STRESS_ROUNDS"] = str(stress_rounds)

    venv = Path(venv_bin)

    # 1. ruff — fail only if the change INTRODUCED new violations
    def _ruff_count() -> int:
        proc = _run([str(venv / "ruff"), "check", ".", "--output-format", "concise"], repo, 180)
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if "All checks passed" in text:
            return 0
        import re

        m = re.search(r"Found (\d+) error", text)
        if m:
            return int(m.group(1))
        n = sum(1 for ln in text.splitlines() if re.match(r"^\S+:\d+:\d+: [A-Z]\d+", ln))
        return n if n else -1

    after = _ruff_count()
    if after < 0:
        return "FAIL: ruff invocation error"
    if after > ruff_baseline:
        proc = _run([str(venv / "ruff"), "check", ".", "--output-format", "concise"], repo, 180)
        return f"FAIL: ruff introduced {after - ruff_baseline} new violation(s)\n" + (proc.stdout or "")[:1500]

    # 2. i18n extract/update/compile
    pot = str(Path(repo) / "locales" / "messages.pot")
    steps = [
        [str(venv / "pybabel"), "extract", "-F", "babel.cfg", "-o", pot, "src/"],
        [str(venv / "pybabel"), "update", "-i", pot, "-d", "locales", "-l", "en"],
        [str(venv / "pybabel"), "update", "-i", pot, "-d", "locales", "-l", "zh"],
        [str(venv / "pybabel"), "compile", "-d", "locales"],
    ]
    for step in steps:
        proc = _run(step, repo, 180)
        if proc.returncode != 0:
            return f"FAIL: pybabel {step[1]}\n" + (proc.stderr or proc.stdout)[:1200]

    # 3. boot-check sweep — subprocess with the repo's venv python so imports resolve
    here = Path(__file__).resolve().parent
    boot = _run([str(venv / "python"), str(here / "bootcheck.py")], repo, 600)
    if boot.returncode != 0:
        return "FAIL: boot-check\n" + (boot.stdout or boot.stderr)[:1500]

    return "PASS: ruff + pybabel + boot-check clean"
