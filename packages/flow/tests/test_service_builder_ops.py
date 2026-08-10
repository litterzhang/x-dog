"""The deterministic half of the service-builder example.

These functions are what make repeated runs converge instead of circling, so
they are tested against real files rather than stubbed. The flow-level suite
(`service_builder.test.json`) covers the graph shape; this covers the judgement
that graph delegates to code precisely so no model makes it.
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


def _plan(ws: Path, text: str) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "PLAN.md").write_text(text, encoding="utf-8")


def test_a_fresh_workspace_reports_no_plan(tmp_path: Path) -> None:
    out = builder_ops.survey(_Ctx(tmp_path / "ws"))

    assert out["has_plan"] == "no"
    assert out["open_count"] == 0
    assert out["run_no"] == 1


def test_survey_counts_what_is_left(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _plan(ws, "- [x] 1. done\n- [ ] 2. open\n- [ ] 3. also open\n- [!] 4. blocked\n")

    out = builder_ops.survey(_Ctx(ws))

    assert out["has_plan"] == "yes"
    assert out["open_count"] == 2
    assert out["blocked_count"] == 1


def test_pick_takes_the_first_open_task_not_the_easiest(tmp_path: Path) -> None:
    """Order is decided once, when the plan is written. Re-choosing every run
    lets a model keep picking the task it likes and never finish the hard one."""
    ws = tmp_path / "ws"
    _plan(ws, "- [x] 1. done\n- [ ] 2. the hard one\n- [ ] 3. the easy one\n")

    assert builder_ops.pick(_Ctx(ws), 2)["task"] == "2. the hard one"


def test_pick_reports_nothing_left_rather_than_inventing_work(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _plan(ws, "- [x] 1. done\n- [x] 2. done\n")

    out = builder_ops.pick(_Ctx(ws), 0)

    assert out["found"] == "no"
    assert out["task"] == ""


def test_unverified_is_reported_as_failure_not_success(tmp_path: Path) -> None:
    """The distinction the whole design rests on: "nothing checked it" and "it
    passed" must never produce the same answer, or the loop terminates on the
    absence of a test suite."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)

    out = builder_ops.verify(_Ctx(ws), "any task")

    assert out["passed"] == "no"
    assert "nothing checked" in out["report"]

    (ws / "verify.txt").write_text("   \n", encoding="utf-8")
    assert builder_ops.verify(_Ctx(ws), "any task")["passed"] == "no", "empty counts as unchecked"


def test_verify_runs_the_projects_own_command(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "verify.txt").write_text("exit 0\n", encoding="utf-8")
    assert builder_ops.verify(_Ctx(ws), "t")["passed"] == "yes"

    (ws / "verify.txt").write_text("echo boom >&2; exit 1\n", encoding="utf-8")
    failed = builder_ops.verify(_Ctx(ws), "t")
    assert failed["passed"] == "no"
    assert "boom" in failed["report"]


def test_a_failed_task_is_blocked_so_the_next_run_moves_on(tmp_path: Path) -> None:
    """Left open, the same task would be picked again every run forever. Marked
    blocked, the loop advances and a human sees a list of what it could not do."""
    ws = tmp_path / "ws"
    _plan(ws, "- [ ] 1. impossible\n- [ ] 2. next\n")

    builder_ops.record(_Ctx(ws), "1. impossible", "no", "boom")

    assert "- [!] 1. impossible" in (ws / "PLAN.md").read_text()
    assert builder_ops.pick(_Ctx(ws), 1)["task"] == "2. next"


def test_a_passed_task_is_ticked(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _plan(ws, "- [ ] 1. add health\n")

    builder_ops.record(_Ctx(ws), "1. add health", "yes", "ok")

    assert "- [x] 1. add health" in (ws / "PLAN.md").read_text()


def test_runs_that_change_no_source_are_counted_and_eventually_stall(tmp_path: Path) -> None:
    """The termination guarantee. Without it a scheduled workflow spends money
    every half hour producing nothing, and looks healthy while doing it."""
    ws = tmp_path / "ws"
    _plan(ws, "- [ ] 1. a\n- [ ] 2. b\n- [ ] 3. c\n")

    def a_run_that_changes_nothing(task: str) -> None:
        builder_ops.survey(_Ctx(ws))           # stamps the tree as it starts
        builder_ops.record(_Ctx(ws), task, "no", "nope")

    a_run_that_changes_nothing("1. a")
    assert builder_ops.survey(_Ctx(ws))["stalled"] == "no", "one idle run is normal"

    a_run_that_changes_nothing("2. b")
    assert builder_ops.survey(_Ctx(ws))["stalled"] == "yes", "two in a row is a stall"


def test_writing_a_source_file_clears_the_stall(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _plan(ws, "- [ ] 1. a\n- [ ] 2. b\n")
    for task in ("1. a", "2. b"):
        builder_ops.survey(_Ctx(ws))
        builder_ops.record(_Ctx(ws), task, "no", "nope")
    assert builder_ops.survey(_Ctx(ws))["stalled"] == "yes"

    _plan(ws, "- [ ] 3. c\n")
    builder_ops.survey(_Ctx(ws))
    (ws / "app.py").write_text("print('real work')\n", encoding="utf-8")   # during the run
    builder_ops.record(_Ctx(ws), "3. c", "yes", "ok")

    assert builder_ops.survey(_Ctx(ws))["stalled"] == "no"


def test_bookkeeping_alone_does_not_count_as_progress(tmp_path: Path) -> None:
    """The journal grows every run by construction. If it counted, the stall
    detector would never fire and the loop would run until someone noticed."""
    ws = tmp_path / "ws"
    _plan(ws, "- [ ] 1. a\n")
    builder_ops.survey(_Ctx(ws))
    builder_ops.record(_Ctx(ws), "1. a", "no", "nope")
    first = json.loads((ws / "state.json").read_text())["fingerprint"]

    _plan(ws, "- [ ] 2. b\n")
    builder_ops.survey(_Ctx(ws))
    builder_ops.record(_Ctx(ws), "2. b", "no", "nope")
    second = json.loads((ws / "state.json").read_text())

    assert second["fingerprint"] == first, "plan/journal/state churn is not progress"
    assert second["idle_runs"] == 2


def test_state_survives_a_corrupt_file(tmp_path: Path) -> None:
    """A half-written state.json from a killed run must not end the project."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "state.json").write_text("{not json", encoding="utf-8")

    assert builder_ops.survey(_Ctx(ws))["run_no"] == 1
