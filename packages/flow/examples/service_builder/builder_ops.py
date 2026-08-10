"""Deterministic half of the self-building service workflow.

Everything here is a plain function so it can be read, tested and trusted. The
division is deliberate: **the model proposes, this module decides.** Progress,
convergence and stalling are all judged by code, because a workflow that asks an
agent "are we done?" gets "yes" eventually regardless of the truth.

State lives in the workspace, not in anyone's context. Each run starts cold, so
anything that must survive is written down.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

PLAN = "PLAN.md"
STATE = "state.json"
JOURNAL = "JOURNAL.md"

# A task line: "- [ ] 3. Add /health endpoint" / "- [x] ..." / "- [!] ..." (blocked)
_TASK = re.compile(r"^- \[(?P<mark>[ x!])\]\s*(?P<body>.+)$")


def _state(ws: Path) -> dict[str, Any]:
    path = ws / STATE
    if not path.exists():
        return {"runs": 0, "fingerprint": "", "idle_runs": 0}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"runs": 0, "fingerprint": "", "idle_runs": 0}
    return loaded if isinstance(loaded, dict) else {"runs": 0, "fingerprint": "", "idle_runs": 0}


def _tasks(ws: Path) -> list[tuple[str, str]]:
    """[(mark, body)] from PLAN.md, in file order. Empty when there is no plan."""
    path = ws / PLAN
    if not path.exists():
        return []
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _TASK.match(line.strip())
        if m:
            out.append((m.group("mark"), m.group("body").strip()))
    return out


def _fingerprint(ws: Path) -> str:
    """A digest of the source tree, so "did this run change anything" is a fact.

    Only the workspace's own files, sorted, content-hashed. `runtime` bookkeeping
    is excluded: the journal changes every run by construction, so including it
    would make every run look productive and the stall detector would never fire.
    """
    digest = hashlib.sha256()
    for path in sorted(ws.rglob("*")):
        if not path.is_file() or path.name in (STATE, JOURNAL, PLAN):
            continue
        if any(part in {".git", "__pycache__", ".venv", "node_modules"} for part in path.parts):
            continue
        digest.update(str(path.relative_to(ws)).encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:16]


def survey(ctx: Any) -> dict[str, Any]:
    """What is true at the start of this run. The only entry point that reads
    everything, so every later node works from one consistent snapshot."""
    ws = Path(ctx.workspace)
    ws.mkdir(parents=True, exist_ok=True)
    state = _state(ws)
    tasks = _tasks(ws)
    # Stamp the tree as it is *now*, so `record` can answer "did this run change
    # anything" exactly. Comparing against the previous run's end instead made
    # the very first run always look productive, because there was no previous
    # value to differ from -- so a project that never started never stalled.
    state["run_start"] = _fingerprint(ws)
    (ws / STATE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    open_tasks = [b for mark, b in tasks if mark == " "]
    blocked = [b for mark, b in tasks if mark == "!"]
    return {
        "has_plan": "yes" if tasks else "no",
        "open_count": len(open_tasks),
        "blocked_count": len(blocked),
        # Two consecutive runs that changed no source file. One is normal (a run
        # can legitimately end in a blocked task); two means the loop is spinning
        # and a human should look rather than the schedule burning tokens.
        "stalled": "yes" if int(state.get("idle_runs", 0)) >= 2 else "no",
        "run_no": int(state.get("runs", 0)) + 1,
        "summary": (
            f"run {int(state.get('runs', 0)) + 1}: "
            f"{len(open_tasks)} open, {len(blocked)} blocked, "
            f"{len([m for m, _ in tasks if m == 'x'])} done"
        ),
    }


def pick(ctx: Any, open_count: int) -> dict[str, Any]:
    """The next task, chosen by position rather than by judgement.

    The plan is ordered once, when it is written; picking the first open task
    keeps that ordering meaningful. Letting an agent choose each run invites it
    to keep picking the easy one.
    """
    _ = open_count
    ws = Path(ctx.workspace)
    for mark, body in _tasks(ws):
        if mark == " ":
            return {"task": body, "found": "yes"}
    return {"task": "", "found": "no"}


def verify(ctx: Any, task: str) -> dict[str, Any]:
    """Run the project's own checks. The one judgement no agent makes.

    Reads the command from `verify.txt` in the workspace, which the planning step
    writes. No command means unverified -- reported as a failure, because
    "nothing checked it" and "it passed" must never look the same.
    """
    ws = Path(ctx.workspace)
    cmd_file = ws / "verify.txt"
    if not cmd_file.exists():
        return {"passed": "no", "report": "no verify.txt: nothing checked this work"}
    command = cmd_file.read_text(encoding="utf-8").strip()
    if not command:
        return {"passed": "no", "report": "verify.txt is empty: nothing checked this work"}
    try:
        proc = subprocess.run(  # noqa: S602 - the project's own test command, by design
            command, shell=True, cwd=ws, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"passed": "no", "report": f"verify timed out after 600s: {command}"}
    tail = (proc.stdout + proc.stderr)[-4000:]
    return {
        "passed": "yes" if proc.returncode == 0 else "no",
        "report": f"$ {command}\nexit={proc.returncode}\n{tail}",
    }


def record(ctx: Any, task: str, passed: str, report: str) -> dict[str, Any]:
    """Write down what happened, and decide whether the run made progress.

    A passing task is ticked; a failing one is marked blocked rather than left
    open, so the next run moves on instead of retrying the same wall forever.
    """
    ws = Path(ctx.workspace)
    mark = "x" if passed == "yes" else "!"
    plan_path = ws / PLAN
    if plan_path.exists() and task:
        lines = plan_path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = _TASK.match(line.strip())
            if m and m.group("mark") == " " and m.group("body").strip() == task:
                lines[i] = line.replace("- [ ]", f"- [{mark}]", 1)
                break
        plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    state = _state(ws)
    now = _fingerprint(ws)
    started_at = state.get("run_start")
    changed = now != started_at if started_at is not None else False
    state["runs"] = int(state.get("runs", 0)) + 1
    state["fingerprint"] = now
    state["idle_runs"] = 0 if changed else int(state.get("idle_runs", 0)) + 1
    (ws / STATE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with (ws / JOURNAL).open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n## run {state['runs']} — {task or '(no task)'}\n\n"
            f"- outcome: {'passed' if passed == 'yes' else 'blocked'}\n"
            f"- source changed: {'yes' if changed else 'no'}\n\n"
            f"```\n{report[:2000]}\n```\n"
        )
    return {
        "outcome": f"{'done' if passed == 'yes' else 'blocked'}: {task}",
        "changed": "yes" if changed else "no",
    }
