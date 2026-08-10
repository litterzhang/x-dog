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


#: Directories that are never part of the product being built.
_NOISE = {".git", ".flow", "__pycache__", ".venv", ".pytest_cache", "node_modules", ".mypy_cache"}


def _source_roots(ctx: Any) -> tuple[Path, ...]:
    """Where the service being built actually lives.

    The granted trees, not the workspace. The workspace holds this workflow's own
    bookkeeping -- the charter, the journal, the run counters -- and measuring
    that would be measuring ourselves: those files change every run by
    construction, so every run would look productive and the halt condition would
    never fire. An unattended timer would bill forever while looking healthy.

    Falls back to the workspace when nothing was granted, so the example still
    works when someone runs it without `--allow-path`.
    """
    granted = tuple(Path(p) for p in getattr(ctx, "allow_paths", ()) or ())
    return granted or (Path(ctx.workspace),)


def _fingerprint(roots: tuple[Path, ...]) -> str:
    """A digest of the product tree: does it differ from when this run started?"""
    digest = hashlib.sha256()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part in _NOISE for part in path.parts):
                continue
            digest.update(str(path.relative_to(root)).encode())
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
    state["run_start"] = _fingerprint(_source_roots(ctx))
    _write_state(ws, state)

    # The agent is briefed that its *workspace* is where its files belong, which
    # is true of this workflow's bookkeeping and wrong for the service. Naming
    # the product directory explicitly is the only thing that separates them --
    # the first run without this put the whole skeleton in the workspace, which
    # is exactly what it had been told to do.
    source_dir = str(_source_roots(ctx)[0])
    has_charter = "yes" if criteria else "no"
    active = "no" if halted else "yes"
    return {
        "active": active,
        "has_charter": has_charter,
        "source_dir": source_dir,
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
    where = _source_roots(ctx)[0]
    cmd_file = ws / VERIFY
    command = cmd_file.read_text(encoding="utf-8").strip() if cmd_file.exists() else ""
    if not command:
        return {"passed": "no", "report": f"no usable {VERIFY}: nothing checked this work"}
    try:
        proc = subprocess.run(  # noqa: S602 - the project's own test command, by design
            command, shell=True, cwd=where, capture_output=True, text=True, timeout=1800,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"passed": "no", "report": f"verify could not complete: {exc}"}
    tail = (proc.stdout + proc.stderr)[-4000:]
    return {
        "passed": "yes" if proc.returncode == 0 else "no",
        "report": f"$ {command}\nexit={proc.returncode}\n{tail}",
    }


def _tested_slugs(ctx: Any) -> set[str]:
    """Criterion slugs a test file explicitly claims to cover.

    The convention is a comment `# covers: slug` (or several, comma-separated)
    beside the test. It is opt-in on purpose: a criterion is credited because
    some test says it checks it, never because the suite was green and the slug
    existed. The second rule would let one passing smoke test close the charter.
    """
    slugs: set[str] = set()
    for root in _source_roots(ctx):
        if not root.exists():
            continue
        for path in root.rglob("test_*.py"):
            if any(part in _NOISE for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r"#[ \t]*covers:[ \t]*([a-z0-9,\- \t]+)", text):
                slugs.update(s.strip() for s in m.group(1).split(",") if s.strip())
    return slugs


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

    # Credit the named criterion, and any other whose own test is now passing.
    #
    # The suite is run whole, so a green run is evidence about every criterion a
    # test covers -- not only the one this run happened to name. Ticking one at a
    # time made the charter lag reality badly: on the first project it read 4 of
    # 12 while six more were implemented, tested and green, and the loop would
    # have spent five hours and a few hundred thousand tokens re-confirming
    # finished work. A criterion still needs a test that names it, so this
    # credits evidence rather than assuming it.
    also: list[str] = []
    if passed == "yes":
        also = [slug for slug in _tested_slugs(ctx) if slug in known and slug != criterion]
    closing = ({criterion} if achieved else set()) | set(also)
    if closing:
        path = ws / CHARTER
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = _CRITERION.match(line.strip())
            if m and m.group("mark") == " " and m.group("slug") in closing:
                lines[i] = line.replace("- [ ]", "- [x]", 1)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        achieved = achieved or bool(also)

    changed = _fingerprint(_source_roots(ctx)) != state.get("run_start")
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


def commit(ctx: Any, outcome: str, halted: str) -> dict[str, Any]:
    """Record the run in version control, and push if the remote is reachable.

    Without this the builder writes files and nothing remembers which run wrote
    them. A journal entry saying "source changed: yes" is not a record you can
    read, revert, or bisect — and an unattended process is exactly the one whose
    work you will later want to undo one increment at a time.

    Every outcome is committed, including failures. A run that tried and could
    not is the most interesting entry in the history, and dropping it would make
    the log claim a smooth ascent that did not happen.

    Git problems are reported, never raised. The work is on disk either way, and
    failing the run over an unreachable remote would burn a barren run and move
    the project closer to halting for a reason that has nothing to do with it.
    """
    root = _source_roots(ctx)[0]
    if not (root / ".git").exists():
        return {"committed": "not a git repository"}

    def git(*args: str, timeout: int = 120) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            return 1, str(exc)
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    code, out = git("add", "-A")
    if code != 0:
        return {"committed": f"git add failed: {out[:200]}"}
    if git("diff", "--cached", "--quiet")[0] == 0:
        return {"committed": "nothing to commit"}

    runs = int(_state(Path(ctx.workspace)).get("runs", 0))
    subject = f"run {runs}: {outcome}"[:72]
    body = (
        f"Written by the unattended builder, not by a person.\n\n"
        f"{outcome}\n"
    )
    if halted:
        body += f"\nThe project halted after this run: {halted}\n"
    code, out = git("commit", "-m", subject, "-m", body)
    if code != 0:
        return {"committed": f"git commit failed: {out[:200]}"}

    code, out = git("push", "origin", "HEAD", timeout=180)
    if code != 0:
        return {"committed": f"committed locally; push failed: {out[:200]}"}
    return {"committed": f"committed and pushed: {subject}"}
