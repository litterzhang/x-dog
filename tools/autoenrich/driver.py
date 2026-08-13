#!/usr/bin/env python3
"""xdog auto-enrich driver — one cycle per invocation.

Runs the ``enrich.json`` flow workflow (build -> scope -> gate -> validate) in
process via ``flow.executor.execute``, then owns the side-effects the flow engine
deliberately does NOT: the working-tree precheck and the deterministic git
commit / revert.

The BUILD agent implements the task md; ``scope`` collects the diff and reverts
out-of-scope tracked edits; ``gate`` runs ruff + mypy --strict + pytest;
``validate`` reviews the diff. Only when gate says PASS and the validator
approves does the driver make a LOCAL commit (never a push). Any rejection
reverts the in-scope working tree. ``AE_DRY_RUN=1`` runs the whole pipeline but
reverts instead of committing.

Safety: this driver only ever reverts *tracked* edits under the allow-list
(``git checkout -- <allow>``). It never runs ``git clean`` / deletes untracked
files, so un-committed work elsewhere in the tree is never destroyed.

Usage::

    uv run python tools/autoenrich/driver.py --task tools/autoenrich/tasks/p1_retry.md
    AE_DRY_RUN=1 uv run python tools/autoenrich/driver.py --task <task.md>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # tools/autoenrich -> tools -> repo root
sys.path.insert(0, str(HERE))  # so the workflow's run: "nodes:scope" resolves


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout
    )


def _tree_dirty(allow: list[str]) -> list[str]:
    """In-scope tracked changes only — out-of-scope noise (e.g. uv.lock) is ignored."""
    out = _git("status", "--porcelain").stdout
    paths = [ln[3:].strip() for ln in out.splitlines() if ln.strip()]
    return [p for p in paths if any(p == a or p.startswith(a) for a in allow)]


def _revert(allow: list[str]) -> None:
    """Restore in-scope tracked edits. NEVER deletes untracked files (no git clean)."""
    tracked = [ln[3:].strip() for ln in _git("status", "--porcelain").stdout.splitlines() if ln.strip()]
    in_scope = [p for p in tracked if any(p == a or p.startswith(a) for a in allow)]
    if in_scope:
        _git("checkout", "--", *in_scope)


async def _run_workflow(task_text: str, allow_paths: str) -> dict[str, Any]:
    """Execute enrich.json in-process; return the runtime container."""
    import ai
    from agent.helpers import stream_fn_from_provider, web_search_fn_from_provider
    from flow.executor import execute
    from flow.loader import load_workflow

    wf = load_workflow(HERE / "enrich.json")
    provider = ai.provider(os.environ.get("AE_PROVIDER", "copilot"))
    base_stream = stream_fn_from_provider(provider)

    def _sf(model: str) -> Any:
        return base_stream

    def _wsf(model: str) -> Any:
        return web_search_fn_from_provider(provider, model)

    builder_prompt = (HERE / "prompts" / "builder.md").read_text(encoding="utf-8")
    validator_prompt = (HERE / "prompts" / "validator.md").read_text(encoding="utf-8")

    inputs = {
        "repo": str(REPO),
        "task": task_text,
        "allow_paths": allow_paths,
        "builder_prompt": builder_prompt,
        "validator_prompt": validator_prompt,
    }
    result = await execute(
        wf,
        stream_fn_factory=_sf,
        web_search_fn_factory=_wsf,
        timeout=float(os.environ.get("AE_NODE_TIMEOUT", "1200")),
        base_dir=HERE,
        inputs=inputs,
    )
    runtime: dict[str, Any] = result.runtime
    return runtime


def _last_json_object(text: str) -> dict[str, Any]:
    """Return the last balanced {...} that parses and has a 'verdict' key."""
    depth, start = 0, -1
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
    for cand in reversed(spans):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict) and "verdict" in obj:
                return obj
        except json.JSONDecodeError:
            continue
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(prog="autoenrich-driver")
    ap.add_argument("--task", required=True, help="Path to the task markdown file.")
    ap.add_argument("--allow", default="", help="Override allow_paths (default: packages/flow/).")
    args = ap.parse_args()

    dry = os.environ.get("AE_DRY_RUN") == "1"
    task_path = Path(args.task)
    if not task_path.is_absolute():
        task_path = REPO / task_path
    task_text = task_path.read_text(encoding="utf-8")
    allow_paths = args.allow or os.environ.get("AE_ALLOW_PATHS", "packages/flow/")
    allow = [p for p in allow_paths.replace(",", " ").split() if p]

    _log("=== auto-enrich cycle start ===")
    if dry:
        _log("AE_DRY_RUN=1: build agent still calls the real LLM; the final commit is skipped (revert instead).")

    # PRECHECK — no in-scope tracked changes yet, else we can't attribute the diff.
    dirty = _tree_dirty(allow)
    if dirty:
        _log(f"in-scope tree not clean ({dirty[:5]}); aborting. Commit or stash first.")
        return 0

    head_before = _git("rev-parse", "HEAD").stdout.strip()
    _log(f"HEAD={head_before[:10]} task={task_path.name} allow={allow}")

    # RUN FLOW
    try:
        runtime = asyncio.run(_run_workflow(task_text, allow_paths))
    except Exception as exc:  # noqa: BLE001 — any workflow failure reverts and reports
        _log(f"workflow error: {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        _revert(allow)
        return 0

    state = runtime.get("state", {})
    scope = json.loads(state.get("scope", {}).get("scope", "{}") or "{}")
    verdict = state.get("gate", {}).get("verdict", "")
    review_raw = state.get("validate", {}).get("review", "")
    report = state.get("build", {}).get("report", "")

    _log(f"BUILD report (first 500 chars):\n{report[:500]}")
    _log(f"SCOPE changed: {scope.get('changed')}")

    # DECISION — deterministic; the flow engine never commits.
    if not scope.get("changed"):
        _log("builder made no in-scope changes; revert (no-op).")
        _revert(allow)
        return 0
    if not verdict.startswith("PASS"):
        _log(f"GATE rejected: {verdict[:600]}")
        _revert(allow)
        return 0
    review = _last_json_object(review_raw)
    approved = str(review.get("verdict", "")).strip().lower() in ("approve", "approved", "pass", "accept", "ok")
    if not approved:
        _log(f"VALIDATOR rejected: {review.get('reasons') or review_raw[:400]}")
        _revert(allow)
        return 0

    _log(f"ALL PASSED. Changed: {scope.get('changed')}")
    if dry:
        _log("DRY_RUN: would commit. Reverting, tree left as HEAD.")
        _revert(allow)
        _log("=== dry-run complete ===")
        return 0

    # COMMIT (LOCAL ONLY — never push)
    _git("add", *allow)
    staged = _git("diff", "--cached", "--name-only").stdout.strip()
    if not staged:
        _log("nothing staged after add; revert.")
        _revert(allow)
        return 0
    task_title = task_path.stem
    reasons = str(review.get("reasons", ""))[:400]
    msg = (
        f"feat(flow): {task_title} (autonomous auto-enrich)\n\n"
        f"Implemented by the xdog auto-enrich workflow "
        f"(build + scope + gate[ruff/mypy/pytest] + validate).\n"
        f"Validator: {reasons}\n"
    )
    commit = _git("commit", "-m", msg)
    if commit.returncode != 0:
        _log(f"commit failed: {commit.stderr[:400]}; revert.")
        _revert(allow)
        return 0
    new_head = _git("rev-parse", "HEAD").stdout.strip()
    _log(f"COMMITTED (local, not pushed) {head_before[:10]} -> {new_head[:10]}")
    _log("=== cycle complete (local commit; review then push yourself) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
