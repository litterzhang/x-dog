"""The deterministic half of the unattended service-builder.

With nobody watching, these functions are the entire safety story: what counts as
progress, what closes a criterion, and when the project stops. They are tested
against real files rather than stubbed, because the flow-level suite necessarily
stubs them and would then be asserting on fiction.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "service_builder"))

import builder_ops  # noqa: E402


class _Ctx:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.step = 0
        self.node_id = "n"
        self.workflow_name = "service-builder"


def _charter(ws: Path, text: str) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "ACCEPTANCE.md").write_text(text, encoding="utf-8")


def _run(ws: Path, criterion: str, passed: str, *, touches: str | None = None) -> dict:
    """One whole run: survey stamps the tree, work happens, record judges it."""
    builder_ops.survey(_Ctx(ws))
    if touches is not None:
        (ws / touches).write_text(f"# {criterion}\n", encoding="utf-8")
    return builder_ops.record(_Ctx(ws), criterion, f"do {criterion}", passed, "report")


def test_a_fresh_workspace_has_no_charter(tmp_path: Path) -> None:
    out = builder_ops.survey(_Ctx(tmp_path / "ws"))

    assert out["has_charter"] == "no"
    assert out["active"] == "yes"
    assert out["run_no"] == 1


def test_survey_lists_the_unmet_criteria_for_the_agent(tmp_path: Path) -> None:
    """The agent chooses from this list, so what is in it is what can be chosen."""
    ws = tmp_path / "ws"
    _charter(ws, "- [x] a: done already\n- [ ] b: still open\n- [ ] c: also open\n")

    out = builder_ops.survey(_Ctx(ws))

    assert out["unmet_count"] == 2
    assert "b: still open" in out["unmet"]
    assert "a: done already" not in out["unmet"]


def test_a_criterion_closes_only_when_the_checks_pass(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _charter(ws, "- [ ] a: thing\n")

    _run(ws, "a", "no", touches="src.py")
    assert "- [ ] a: thing" in (ws / "ACCEPTANCE.md").read_text(), "failing leaves it open"

    _run(ws, "a", "yes", touches="src2.py")
    assert "- [x] a: thing" in (ws / "ACCEPTANCE.md").read_text()


def test_an_invented_criterion_closes_nothing(tmp_path: Path) -> None:
    """The unattended failure mode: an agent could otherwise finish the project
    by naming a criterion that does not exist, at the moment nobody is reading."""
    ws = tmp_path / "ws"
    _charter(ws, "- [ ] a: thing\n")

    out = _run(ws, "totally-made-up", "yes", touches="src.py")

    assert "- [ ] a: thing" in (ws / "ACCEPTANCE.md").read_text()
    assert "unknown criterion" in out["outcome"]
    assert builder_ops.survey(_Ctx(ws))["unmet_count"] == 1


def test_meeting_every_criterion_halts_the_project(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _charter(ws, "- [ ] a: one\n- [ ] b: two\n")

    _run(ws, "a", "yes", touches="a.py")
    assert builder_ops.survey(_Ctx(ws))["active"] == "yes"

    _run(ws, "b", "yes", touches="b.py")
    done = builder_ops.survey(_Ctx(ws))

    assert done["active"] == "no"
    assert "complete" in done["status"]


def test_a_halted_run_is_cheap_and_stays_halted(tmp_path: Path) -> None:
    """Nobody uninstalls the timer on a finished project, so the finished state
    has to be a no-op rather than a thing that keeps calling models."""
    ws = tmp_path / "ws"
    _charter(ws, "- [ ] a: one\n")
    _run(ws, "a", "yes", touches="a.py")

    for _ in range(3):
        assert builder_ops.survey(_Ctx(ws))["active"] == "no"


def test_runs_that_change_no_file_halt_the_project(tmp_path: Path) -> None:
    """The bound that makes an unattended loop safe to leave installed."""
    ws = tmp_path / "ws"
    _charter(ws, "- [ ] a: one\n- [ ] b: two\n- [ ] c: three\n")

    _run(ws, "a", "no")
    assert builder_ops.survey(_Ctx(ws))["active"] == "yes", "one idle run is normal"

    _run(ws, "b", "no")
    stopped = builder_ops.survey(_Ctx(ws))

    assert stopped["active"] == "no"
    assert "changed no source file" in stopped["status"]


def test_motion_without_achievement_also_halts(tmp_path: Path) -> None:
    """The subtler stall: every run edits files, so the idle counter never fires,
    but no criterion is ever met. Left alone this bills forever while looking
    healthy — a diff every hour and nothing to show for it."""
    ws = tmp_path / "ws"
    _charter(ws, "- [ ] a: one\n- [ ] b: two\n- [ ] c: three\n- [ ] d: four\n- [ ] e: five\n")

    for i in range(builder_ops.MAX_BARREN_RUNS):
        _run(ws, "a", "no", touches=f"churn{i}.py")

    stopped = builder_ops.survey(_Ctx(ws))

    assert stopped["active"] == "no"
    assert "no acceptance criterion" in stopped["status"]


def test_meeting_a_criterion_resets_the_barren_counter(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _charter(ws, "- [ ] a: one\n- [ ] b: two\n- [ ] c: three\n")

    _run(ws, "a", "no", touches="x1.py")
    _run(ws, "a", "no", touches="x2.py")
    _run(ws, "a", "yes", touches="x3.py")

    assert json.loads((ws / "state.json").read_text())["barren_runs"] == 0
    assert builder_ops.survey(_Ctx(ws))["active"] == "yes"


def test_unverified_work_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    """If "nothing checked it" and "it passed" agreed, an unattended loop would
    close fastest on a project that never wrote a test."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)

    assert builder_ops.verify(_Ctx(ws), "a")["passed"] == "no"

    (ws / "verify.txt").write_text("  \n", encoding="utf-8")
    assert builder_ops.verify(_Ctx(ws), "a")["passed"] == "no"

    (ws / "verify.txt").write_text("exit 0\n", encoding="utf-8")
    assert builder_ops.verify(_Ctx(ws), "a")["passed"] == "yes"


def test_bookkeeping_alone_is_not_progress(tmp_path: Path) -> None:
    """ACCEPTANCE/JOURNAL/state change every run by construction. Counting them
    would mean the idle bound never fires."""
    ws = tmp_path / "ws"
    _charter(ws, "- [ ] a: one\n- [ ] b: two\n")

    _run(ws, "a", "no")
    _run(ws, "b", "no")

    assert json.loads((ws / "state.json").read_text())["idle_runs"] == 2


def test_a_corrupt_state_file_does_not_end_the_project(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "state.json").write_text("{ half written", encoding="utf-8")

    out = builder_ops.survey(_Ctx(ws))

    assert out["run_no"] == 1
    assert out["active"] == "yes"
