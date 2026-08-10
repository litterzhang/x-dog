"""Deterministic half of the unattended service-builder workflow.

No human is in this loop, which changes what the code has to guarantee. With a
person watching, "it got stuck" is a notification. Without one, the loop must
**stop itself**, and it must be cheap to keep firing after it stops — a timer
does not know the project is finished.

So everything that decides whether work continues is here, in plain functions,
not in a prompt: what is still unmet, whether the last run achieved anything,
and when to halt. The model proposes the increment; this module decides whether
the increment counted.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

CHARTER = "ACCEPTANCE.md"
STATE = "state.json"
JOURNAL = "JOURNAL.md"
VERIFY = "verify.txt"

#: `- [ ] health-endpoint: GET /health returns 200`
_CRITERION = re.compile(r"^- \[(?P<mark>[ x])\]\s*(?P<slug>[a-z0-9][a-z0-9-]*)\s*:\s*(?P<text>.+)$")

#: Consecutive runs that changed no source file before the project halts. One is
#: normal — a run can legitimately fail its checks. Two means it is spinning.
MAX_IDLE_RUNS = 2

#: Consecutive runs that met no new criterion. Higher than the idle bound because
#: real work can take more than one run: a run may refactor, add scaffolding, or
#: half-build something and still be progressing. But four runs of motion with
#: nothing achieved is not progress, it is a model rewriting the same file.
MAX_BARREN_RUNS = 4


def _defaults() -> dict[str, Any]:
    return {"runs": 0, "idle_runs": 0, "barren_runs": 0, "halted": "", "run_start": None}


def _state(ws: Path) -> dict[str, Any]:
    path = ws / STATE
    if not path.exists():
        return _defaults()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _defaults()  # a half-written file from a killed run must not end the project
    return {**_defaults(), **loaded} if isinstance(loaded, dict) else _defaults()


def _write_state(ws: Path, state: dict[str, Any]) -> None:
    (ws / STATE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _criteria(ws: Path) -> list[tuple[str, str, str]]:
    """[(mark, slug, text)] from ACCEPTANCE.md, in file order."""
    path = ws / CHARTER
    if not path.exists():
        return []
    out: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _CRITERION.match(line.strip())
        if m:
            out.append((m.group("mark"), m.group("slug"), m.group("text").strip()))
    return out


def _fingerprint(ws: Path) -> str:
    """A digest of the source tree — the workspace minus its own bookkeeping.

    ACCEPTANCE/JOURNAL/state change every run by construction. Counting them
    would make every run look productive, so the halt condition would never fire
    and an unattended timer would bill forever while looking healthy.
    """
    digest = hashlib.sha256()
    for path in sorted(ws.rglob("*")):
        if not path.is_file() or path.name in (STATE, JOURNAL, CHARTER):
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
    """One consistent snapshot for the rest of the run, and the halt gate.

    Returns `active: no` when the project is finished or halted, which routes the
    run straight to its end. That path costs no tokens, so leaving the timer
    installed on a finished project is harmless — which matters, because nobody
    is going to remember to uninstall it.
    """
    ws = Path(ctx.workspace)
    ws.mkdir(parents=True, exist_ok=True)
    state = _state(ws)
    criteria = _criteria(ws)
    unmet = [(slug, text) for mark, slug, text in criteria if mark == " "]

    halted = str(state.get("halted") or "")
    if criteria and not unmet and not halted:
        halted = "complete: every acceptance criterion is met"
        state["halted"] = halted
    state["run_start"] = _fingerprint(ws)
    _write_state(ws, state)

    has_charter = "yes" if criteria else "no"
    active = "no" if halted else "yes"
    return {
        "active": active,
        "has_charter": has_charter,
        "unmet_count": len(unmet),
        "unmet": "\n".join(f"- {slug}: {text}" for slug, text in unmet),
        "run_no": int(state.get("runs", 0)) + 1,
        "status": halted or (
            f"run {int(state.get('runs', 0)) + 1}: {len(unmet)} of {len(criteria)} criteria unmet"
            if criteria else "no charter yet"
        ),
    }


def verify(ctx: Any, criterion: str) -> dict[str, Any]:
    """Run the project's own checks. The one judgement no agent makes.

    A missing or empty command is a *failure*, not a pass. If "nothing checked
    it" and "it passed" produced the same answer, an unattended loop would
    terminate fastest on a project that never wrote a test.
    """
    _ = criterion
    ws = Path(ctx.workspace)
    cmd_file = ws / VERIFY
    command = cmd_file.read_text(encoding="utf-8").strip() if cmd_file.exists() else ""
    if not command:
        return {"passed": "no", "report": f"no usable {VERIFY}: nothing checked this work"}
    try:
        proc = subprocess.run(  # noqa: S602 - the project's own test command, by design
            command, shell=True, cwd=ws, capture_output=True, text=True, timeout=1800,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"passed": "no", "report": f"verify could not complete: {exc}"}
    tail = (proc.stdout + proc.stderr)[-4000:]
    return {
        "passed": "yes" if proc.returncode == 0 else "no",
        "report": f"$ {command}\nexit={proc.returncode}\n{tail}",
    }


def record(ctx: Any, criterion: str, task: str, passed: str, report: str) -> dict[str, Any]:
    """Tick what was actually achieved, then decide whether to keep going.

    A criterion is met only when the checks pass **and** the slug the agent named
    exists in the charter. Without that second condition an agent could close the
    project by inventing a criterion, which is the unattended failure mode:
    nobody is reading the output at the moment it happens.
    """
    ws = Path(ctx.workspace)
    state = _state(ws)
    known = {slug for _, slug, _ in _criteria(ws)}
    achieved = passed == "yes" and criterion in known

    if achieved:
        path = ws / CHARTER
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = _CRITERION.match(line.strip())
            if m and m.group("mark") == " " and m.group("slug") == criterion:
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                break
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    changed = _fingerprint(ws) != state.get("run_start")
    state["runs"] = int(state.get("runs", 0)) + 1
    state["idle_runs"] = 0 if changed else int(state.get("idle_runs", 0)) + 1
    state["barren_runs"] = 0 if achieved else int(state.get("barren_runs", 0)) + 1

    if not state.get("halted"):
        if state["idle_runs"] >= MAX_IDLE_RUNS:
            state["halted"] = f"stalled: {MAX_IDLE_RUNS} runs changed no source file"
        elif state["barren_runs"] >= MAX_BARREN_RUNS:
            state["halted"] = f"stalled: {MAX_BARREN_RUNS} runs met no acceptance criterion"
    _write_state(ws, state)

    note = "" if criterion in known else " (named an unknown criterion)"
    with (ws / JOURNAL).open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n## run {state['runs']} — {criterion or '(none)'}\n\n"
            f"- increment: {task}\n"
            f"- checks: {'passed' if passed == 'yes' else 'failed'}{note}\n"
            f"- source changed: {'yes' if changed else 'no'}\n\n"
            f"```\n{report[:2000]}\n```\n"
        )
    return {
        "outcome": (
            f"met {criterion}" if achieved
            else f"no criterion met{note}: {task}"
        ),
        "halted": str(state.get("halted") or ""),
    }
