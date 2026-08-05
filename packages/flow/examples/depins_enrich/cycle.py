"""flow script-node entry points for the depins enrichment workflow.

Every deterministic step of a cycle lives here, reached as ``run: "cycle:<fn>"``
from ``depins_enrich.json``.  There is no driver process any more: precheck, rate
limiting, input hydration, the decision, and the git write-side (commit/push or
revert) are all ordinary nodes in the graph.

Two conventions make that work:

* A node never raises to signal "stop the cycle".  It returns a verdict, and the
  workflow's conditional edges decide what runs next.  A raise means something is
  actually broken, not that the cycle was rejected.
* ``precheck`` is both the gate and the input hydrator.  Everything downstream
  needs (repo, prompts, ruff baseline, ledger) flows out of it, so when it says
  stop, no downstream node has an enabled incoming edge and the whole chain is
  skipped — no flags to thread, no early returns to emulate.

The guard/gate/scope helpers are unchanged from the previous generation; they are
imported from their own modules so this file stays about *cycle* logic.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

# Paths that must never be committed even if git surfaces them as dirty.
_FORBIDDEN_SUBSTR = ("instance/", "translation_cache.json", ".log", ".claude/")

# Paths a cycle is allowed to write. `git clean` is scoped to these so a revert
# never touches unrelated local files, and precheck uses the same list to decide
# what it may reclaim. AE_CHANGE.json is the builder's manifest, normally consumed
# and removed by `scope` — it is left behind only when a cycle dies early.
_CLEAN_SCOPE = ("src/depins/templates/projects/", "src/", "locales/", "AE_CHANGE.json")


class GitError(RuntimeError):
    """A git invocation failed."""


def _git(repo: str, *args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, timeout=timeout
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc


def _porcelain_path(line: str) -> str:
    """Path from a ``git status --porcelain`` line (``XY <path>``).

    Do not strip the line first: the leading column can be a space (`` M`` for
    unstaged-modified) and stripping shifts the slice.
    """
    path = line[3:]
    if " -> " in path:  # rename: take the new path
        path = path.split(" -> ", 1)[1]
    return path.strip()


def _changed_paths(repo: str) -> list[str]:
    out = _git(repo, "status", "--porcelain").stdout
    return [_porcelain_path(ln) for ln in out.split("\n") if ln]


def _untracked(repo: str) -> list[str]:
    return _git(repo, "ls-files", "--others", "--exclude-standard").stdout.split()


def _ruff_count(repo: str, venv_bin: str) -> int:
    """Total ruff violations, or -1 if ruff could not be interpreted."""
    import re

    proc = subprocess.run(
        [str(Path(venv_bin) / "ruff"), "check", ".", "--output-format", "concise"],
        cwd=repo, capture_output=True, text=True, timeout=180,
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if "All checks passed" in text:
        return 0
    match = re.search(r"Found (\d+) error", text)
    if match:
        return int(match.group(1))
    n = sum(1 for ln in text.splitlines() if re.match(r"^\S+:\d+:\d+: [A-Z]\d+", ln))
    return n if n else -1


def _hours_since_last_commit(repo: str) -> float:
    proc = _git(repo, "log", "-1", "--format=%ct", check=False, timeout=30)
    try:
        return (time.time() - int(proc.stdout.strip())) / 3600.0
    except ValueError:
        return 1e9


def _load_set(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return sorted(data) if isinstance(data, list) else []


# ``state_dir`` is a workflow input, not a path derived from ``__file__``: the
# ledger is mutable state that must outlive any one run, while the code is copied
# into a bundle under ~/.local/share.  Deriving it from the module location would
# silently write the ledger inside the bundle and lose it on the next install.
def _ledger_dir(state_dir: str) -> Path:
    return Path(state_dir) / "ledger"


def _prompts_dir(state_dir: str) -> Path:
    return Path(state_dir) / "prompts"


# ---------------------------------------------------------------------------
# 1. precheck — the gate and the input hydrator
# ---------------------------------------------------------------------------
def precheck(
    ctx: Any,
    repo: str,
    venv_bin: str,
    remote: str,
    branch: str,
    state_dir: str,
    min_hours: int,
    stress_rounds: int,
) -> dict[str, object]:
    """Decide whether this cycle may run, and hydrate everything downstream needs.

    Returns ``proceed=False`` (never raises) for the two ordinary reasons to skip:
    a dirty working tree, and the minimum-interval rate limit.  A dirty tree is a
    deliberate stop rather than an automatic revert — someone may be editing the
    repo by hand, and discarding their work to keep a bot running is the wrong
    trade.
    """
    _ = ctx
    blank = {
        "repo": "", "venv_bin": "", "head": "", "ruff_baseline": 0, "known_json": "{}",
        "builder_prompt": "", "validator_prompt": "", "stress_rounds": 0,
        "remote": "", "branch": "", "state_dir": "",
    }

    # `git status --porcelain` already reports untracked entries (`?? path`), so
    # adding _untracked() here would list them twice.
    dirty = _changed_paths(repo)
    if dirty:
        # A cycle that ends without reaching its write side — a repair loop that
        # never converged, a crash between build and commit — leaves its own edits
        # behind. Those paths belong to this loop, so reclaiming them is safe and
        # keeps one bad cycle from wedging every future one. Anything outside them
        # is someone else's work in progress: stop rather than discard it.
        foreign = [p for p in dirty if not any(p.startswith(scope) for scope in _CLEAN_SCOPE)]
        if foreign:
            return {**blank, "proceed": False, "reason": f"working tree not clean: {foreign[:8]}"}
        _git(repo, "reset", "--hard", "HEAD", timeout=60)
        _git(repo, "clean", "-fd", "--", *_CLEAN_SCOPE, check=False, timeout=60)
        if _changed_paths(repo):
            return {**blank, "proceed": False, "reason": "could not reclaim the working tree"}

    try:
        _git(repo, "fetch", remote, branch, timeout=120)
        _git(repo, "rebase", f"{remote}/{branch}", timeout=120)
    except GitError as exc:
        return {**blank, "proceed": False, "reason": f"pull --rebase failed: {exc}"}

    hours = _hours_since_last_commit(repo)
    if hours < float(min_hours) and os.environ.get("AE_DRY_RUN") != "1":
        return {**blank, "proceed": False, "reason": f"rate limited: {hours:.2f}h < {min_hours}h"}

    ledger = _ledger_dir(state_dir)
    known = {
        "projects": _load_set(ledger / "known_projects.json"),
        "slugs": _load_set(ledger / "known_slugs.json"),
        "features": _load_set(ledger / "known_features.json"),
    }
    prompts = _prompts_dir(state_dir)
    builder_prompt = (prompts / "builder.md").read_text(encoding="utf-8") + (
        f"\n\nKnown project keys: {known['projects']}\n"
        f"Known blog slugs: {known['slugs']}\n"
        f"Known feature ids: {known['features']}\n"
        f"\nThe repo is at {repo}. After your ONE change, write AE_CHANGE.json at the repo root."
    )

    baseline = _ruff_count(repo, venv_bin)
    return {
        "proceed": True,
        "reason": "",
        "repo": repo,
        "venv_bin": venv_bin,
        "remote": remote,
        "branch": branch,
        "state_dir": state_dir,
        "head": _git(repo, "rev-parse", "HEAD").stdout.strip(),
        "ruff_baseline": baseline,
        "known_json": json.dumps(known),
        "builder_prompt": builder_prompt,
        "validator_prompt": (prompts / "validator.md").read_text(encoding="utf-8"),
        "stress_rounds": stress_rounds,
    }


# ---------------------------------------------------------------------------
# 2. decide — the single join point every rejection path reaches
# ---------------------------------------------------------------------------
def _last_json_object(text: str) -> dict[str, Any]:
    """Last balanced ``{...}`` span in *text* that parses and carries a verdict.

    Validators reliably prepend prose before their JSON, so scanning for the last
    parseable object with a ``verdict`` key is more robust than any regex.
    """
    depth = 0
    start = -1
    spans: list[str] = []
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append(text[start : i + 1])
    for span in reversed(spans):
        try:
            obj = json.loads(span)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "verdict" in obj:
            return obj
    return {}


_APPROVED = ("approve", "approved", "pass", "accept", "ok")

# Stages whose rejection describes something concrete to change. `guards` is
# deliberately absent: it rejects a change for being out of scope, and a fixer
# that "repairs" that is just laundering a violation.
_FIXABLE_STAGES = ("gate", "validate")


def _review_reason(reviewed: dict[str, Any], raw: str) -> str:
    """Explain a validator rejection in a way that can actually be acted on.

    A bare ``reasons`` field is often absent or empty, which used to surface as
    ``validate: ""`` — a rejection with no information, and the single most common
    outcome of a cycle. Fall back through the verdict itself to a snippet of the
    raw reply, and say explicitly when no JSON verdict was found at all (that means
    the validator did not answer in the required shape, which is a different bug
    from it disagreeing).
    """
    if not reviewed:
        snippet = " ".join((raw or "").split())[:300]
        return f"no JSON verdict in the reply: {snippet or '(empty reply)'}"
    for key in ("reasons", "reason", "explanation", "notes"):
        value = reviewed.get(key)
        if value:
            return json.dumps(value, ensure_ascii=False)[:400]
    return f"verdict={reviewed.get('verdict')!r} with no stated reason: " + json.dumps(
        reviewed, ensure_ascii=False
    )[:300]


def decide(
    ctx: Any,
    scope: str,
    guards: str,
    verdict: str,
    review: str,
    repo: str,
    remote: str,
    branch: str,
    head: str,
    state_dir: str,
) -> dict[str, object]:
    """Fold the four independent judgements into one accept/reject verdict.

    Every stage runs to completion — the gate returns ``FAIL: ...`` text rather
    than raising — so this is the one place a cycle is accepted or rejected, and
    the workflow needs exactly one pair of conditional edges out of it.

    It also passes the repo coordinates through. That is deliberate: it makes
    ``decide`` the *only* predecessor of the write-side nodes, so each of
    ``commit``/``revert`` has exactly one incoming edge and cannot be reached
    while its condition is false.
    """
    _ = ctx
    scope_data = json.loads(scope) if scope else {}
    guards_data = json.loads(guards) if guards else {}
    reviewed = _last_json_object(review)
    coords = {
        "repo": repo, "remote": remote, "branch": branch, "head": head, "state_dir": state_dir,
    }

    def _reject(stage: str, reason: str) -> dict[str, object]:
        return {
            **coords,
            "approved": False,
            "retry": "yes" if stage in _FIXABLE_STAGES else "no",
            "stage": stage,
            "reason": reason,
            "kind": guards_data.get("kind", ""),
            "id": guards_data.get("id", ""),
            "summary": scope_data.get("summary", ""),
        }

    if not scope_data.get("changed"):
        stage, reason = "build", "builder made no changes"
    elif not guards_data.get("ok"):
        stage, reason = "guards", "; ".join(guards_data.get("reasons", [])) or "guards rejected"
    elif not verdict.startswith("PASS"):
        stage, reason = "gate", verdict[:400]
    elif str(reviewed.get("verdict", "")).strip().lower() not in _APPROVED:
        stage, reason = "validate", _review_reason(reviewed, review)
    else:
        return {
            **coords,
            "approved": True,
            "retry": "no",
            "stage": "accepted",
            "reason": str(reviewed.get("reasons", ""))[:400],
            "kind": guards_data.get("kind", "content"),
            "id": guards_data.get("id", ""),
            "summary": scope_data.get("summary") or guards_data.get("id", ""),
        }

    return _reject(stage, reason)


# ---------------------------------------------------------------------------
# 3. the write side — exactly one of these runs, chosen by a conditional edge
# ---------------------------------------------------------------------------
def revert(ctx: Any, repo: str, head: str, stage: str, reason: str) -> dict[str, object]:
    """Restore the tree to *head* and drop the builder's untracked files.

    Scoped to the directories a cycle may write, so a revert never removes
    unrelated local files.
    """
    _ = ctx
    _git(repo, "reset", "--hard", head, timeout=60)
    _git(repo, "clean", "-fd", "--", *_CLEAN_SCOPE, check=False, timeout=60)
    return {
        "outcome": "rejected",
        "detail": f"{stage}: {reason}"[:600],
        "head": head,
    }


def commit(
    ctx: Any,
    repo: str,
    remote: str,
    branch: str,
    head: str,
    kind: str,
    ident: str,
    summary: str,
    reason: str,
    state_dir: str,
) -> dict[str, object]:
    """Stage an explicit path list, commit, rebase, push, and record the ledger.

    ``AE_DRY_RUN=1`` runs everything up to the commit and then reverts, so a full
    cycle — including the real agent calls — can be exercised without touching the
    remote.  A push race is recovered by resetting to the remote rather than
    retrying, so a losing cycle leaves nothing behind.
    """
    _ = ctx
    if os.environ.get("AE_DRY_RUN") == "1":
        changed = _changed_paths(repo)
        _git(repo, "reset", "--hard", head, timeout=60)
        _git(repo, "clean", "-fd", "--", *_CLEAN_SCOPE, check=False, timeout=60)
        return {
            "outcome": "dry_run",
            "detail": f"would commit {kind} {ident!r}; changed: {changed[:12]}",
            "head": head,
        }

    paths = [
        p for p in dict.fromkeys(_changed_paths(repo) + _untracked(repo) + ["locales"])
        if not any(sub in p for sub in _FORBIDDEN_SUBSTR)
    ]
    if not paths:
        _git(repo, "reset", "--hard", head, timeout=60)
        return {"outcome": "rejected", "detail": "nothing safe to stage", "head": head}
    _git(repo, "add", "--", *paths)

    message = (
        f"feat: {summary} (autonomous enrichment)\n\n"
        f"{kind} id: {ident}\n"
        f"Automated flow cycle: build + scope + guards + gate + validate.\n"
        f"Validator: {reason[:400]}\n"
    )
    _git(repo, "commit", "-m", message)
    try:
        _git(repo, "fetch", remote, branch, timeout=120)
        _git(repo, "rebase", f"{remote}/{branch}", timeout=120)
        _git(repo, "push", remote, branch, timeout=180)
    except GitError as exc:
        # Lost a race with another writer: discard our commit entirely rather than
        # leave a diverged local branch for the next cycle's precheck to trip on.
        _git(repo, "fetch", remote, branch, check=False, timeout=120)
        _git(repo, "reset", "--hard", f"{remote}/{branch}", check=False, timeout=60)
        return {"outcome": "rejected", "detail": f"push race, reset to remote: {exc}", "head": head}

    new_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _record_ledger(state_dir, kind, ident)
    return {"outcome": "pushed", "detail": f"{kind} {ident!r} -> {new_head[:10]}", "head": new_head}


def _record_ledger(state_dir: str, kind: str, ident: str) -> None:
    """Add *ident* to the ledger — only ever called after a successful push."""
    if not ident:
        return
    name = {"feature": "known_features", "project": "known_projects"}.get(kind, "known_slugs")
    path = _ledger_dir(state_dir) / f"{name}.json"
    entries = set(_load_set(path))
    entries.add(ident)
    path.write_text(json.dumps(sorted(entries), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. skipped — the precheck-said-no branch, so a cycle always reports an outcome
# ---------------------------------------------------------------------------
def skipped(ctx: Any, reason: str) -> dict[str, object]:
    """Turn a precheck refusal into the same result shape the write side emits."""
    _ = ctx
    return {"outcome": "skipped", "detail": reason, "head": ""}
