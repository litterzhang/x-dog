"""flow.testing.report — render suite results for a terminal.

Kept apart from the checking logic so the CLI owns presentation only.  The format
answers, in order, the three questions someone has when a case fails: *which
expectation broke*, *what actually came out*, and *which nodes were stubbed vs ran
for real* — that last column is usually the fastest route to the cause.
"""

from __future__ import annotations

from xdog.flow.testing.case import CaseResult

_OK = "ok"
_FAIL = "FAIL"


def render_case(result: CaseResult, *, verbose: bool = False) -> list[str]:
    """Lines describing one case: a status row plus, on failure, the evidence."""
    status = _OK if result.ok else _FAIL
    lines = [f"  {result.case.name:<44} {status}   {_summary(result)}"]
    if result.ok and not verbose:
        return lines

    for failure in result.failures:
        lines.append("")
        lines.append(f"    {failure.where}")
        lines.append(f"      expect  {failure.expected}")
        lines.append(f"      actual  {failure.actual}")

    if result.failures or verbose:
        lines.append("")
        lines.extend(_trace(result))
    return lines


def render_suite(name: str, results: list[CaseResult], *, verbose: bool = False) -> list[str]:
    lines = [name]
    for result in results:
        lines.extend(render_case(result, verbose=verbose))
    return lines


def render_total(results: list[CaseResult]) -> str:
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    if failed:
        return f"{passed} passed, {failed} failed"
    return f"{passed} passed"


def _summary(result: CaseResult) -> str:
    if result.outcome == "paused":
        return f"paused at {result.paused_node}"
    if result.outcome == "error":
        return _clip(result.error, 60)
    nodes = len(result.calls)
    fanned = [f"{n} x{c}" for n, c in sorted(result.calls.items()) if c > 1]
    detail = ", ".join(fanned)
    return f"{nodes} nodes" + (f", {detail}" if detail else "")


def _trace(result: CaseResult) -> list[str]:
    """One line per activation, marking stubbed nodes so the seam is visible."""
    lines = ["    trace"]
    for step, node_id in enumerate(result.order):
        mark = "stub" if node_id in result.stubbed else "ran"
        calls = result.calls.get(node_id, 1)
        rounds = result.rounds.get(node_id, 1)
        extra = f" x{calls // rounds}" if rounds and calls // rounds > 1 else ""
        lines.append(f"      step {step:<3} {node_id:<20} {mark}{extra}")
    if not result.order:
        lines.append("      (no node completed)")
    return lines


def _clip(text: str, width: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"
