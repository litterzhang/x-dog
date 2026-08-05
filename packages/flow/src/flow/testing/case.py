"""flow.testing.case — drive one case and judge the outcome.

The case runner owns the seam between "what happened" and "what was expected".  It
runs the workflow through the ordinary :func:`flow.executor.execute` with stubs
installed, classifies the outcome into the same closed three-way choice the suite
uses, and produces failures that name a path rather than dumping two blobs.

Call counts come from :class:`~flow.events.NodeFinished` rather than the trace: a
fan-out node contributes one trace frame for the whole group, so only the event's
``instances`` field can say how many times the node's work actually ran.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from flow.errors import WorkflowPaused
from flow.events import FlowEvent, NodeFinished
from flow.executor import execute
from flow.models import WorkflowDef
from flow.testing.match import first_difference
from flow.testing.models import Case
from flow.testing.stubs import CaseStubs


@dataclass(frozen=True)
class Failure:
    """One unmet expectation, already reduced to the smallest useful statement."""

    where: str  # e.g. "expect.output.risk.status" or "expect.calls"
    expected: str
    actual: str


@dataclass
class CaseResult:
    """Everything a report needs about one executed case."""

    case: Case
    failures: list[Failure] = field(default_factory=list)
    calls: dict[str, int] = field(default_factory=dict)
    rounds: dict[str, int] = field(default_factory=dict)
    output: dict[str, object] = field(default_factory=dict)
    outcome: str = "success"
    error: str = ""
    paused_node: str = ""
    stubbed: set[str] = field(default_factory=set)
    order: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def run_case(wf: WorkflowDef, case: Case, *, base_dir: Path, timeout: float = 30.0) -> CaseResult:
    """Execute *case* against *wf* and check its expectations."""
    return asyncio.run(run_case_async(wf, case, base_dir=base_dir, timeout=timeout))


async def run_case_async(
    wf: WorkflowDef,
    case: Case,
    *,
    base_dir: Path,
    timeout: float = 30.0,
) -> CaseResult:
    stubs = CaseStubs(case=case)
    events: list[FlowEvent] = []
    result = CaseResult(case=case, stubbed=stubs.stubbed_nodes())

    try:
        exec_result = await execute(
            wf,
            timeout=timeout,
            base_dir=base_dir,
            inputs=case.inputs,
            signals=set(case.signals),
            max_tokens=case.max_tokens,
            on_event=events.append,
            stubs=stubs,
        )
        out = exec_result.runtime.get("out", {})
        result.output = dict(out) if isinstance(out, dict) else {}
    except WorkflowPaused as exc:
        result.outcome = "paused"
        result.error = str(exc)
        result.paused_node = exc.node_id
    except Exception as exc:  # a failing run is a legitimate expected outcome
        result.outcome = "error"
        result.error = str(exc) or type(exc).__name__

    _tally(result, events)
    _check_outcome(result)
    _check_output(result)
    _check_calls(result)
    _check_stale_rules(result, stubs)
    return result


def _tally(result: CaseResult, events: Sequence[FlowEvent]) -> None:
    for ev in events:
        if not isinstance(ev, NodeFinished):
            continue
        result.calls[ev.node_id] = result.calls.get(ev.node_id, 0) + ev.instances
        result.rounds[ev.node_id] = result.rounds.get(ev.node_id, 0) + 1
        result.order.append(ev.node_id)


def _check_outcome(result: CaseResult) -> None:
    expect = result.case.expect
    if expect.outcome == "success":
        if result.outcome != "success":
            result.failures.append(Failure("expect.success", "the run completes", result.error))
        return

    if expect.outcome == "error":
        if result.outcome != "error":
            result.failures.append(
                Failure("expect.error", f"a failure containing {expect.error!r}", _describe(result))
            )
        elif expect.error and expect.error not in result.error:
            result.failures.append(Failure("expect.error", f"message containing {expect.error!r}", result.error))
        return

    # paused
    if result.outcome != "paused":
        result.failures.append(
            Failure("expect.paused", f"a pause at {expect.paused!r}", _describe(result))
        )
        return
    actual_node = result.paused_node
    if actual_node != expect.paused:
        result.failures.append(Failure("expect.paused", str(expect.paused), str(actual_node)))


def _check_output(result: CaseResult) -> None:
    expect = result.case.expect
    if not expect.output:
        return
    diff = first_difference(expect.output, result.output, "expect.output")
    if diff is not None:
        where, want, got = diff
        result.failures.append(Failure(where, repr(want), repr(got)))


def _check_calls(result: CaseResult) -> None:
    for node_id, want in result.case.expect.calls.items():
        got = result.calls.get(node_id, 0)
        if got != want:
            result.failures.append(Failure(f"expect.calls.{node_id}", str(want), str(got)))


def _check_stale_rules(result: CaseResult, stubs: CaseStubs) -> None:
    for node_id, i, rule in stubs.unused_rules():
        result.failures.append(
            Failure(f"stubs.{node_id}[{i}]", f"rule {rule.describe()} fires", "never matched")
        )


def _describe(result: CaseResult) -> str:
    if result.outcome == "success":
        return "the run completed"
    if result.outcome == "paused":
        return f"paused: {result.error}"
    return result.error
