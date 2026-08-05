"""flow.testing — companion ``*.test.json`` suites for workflows.

A suite drives a workflow through :func:`flow.executor.execute` with stubs installed
at its non-deterministic boundaries — agent turns, human signals, and (opt-in)
script nodes — while everything else runs for real: edges, conditions, loops,
fan-out, mappings, coercion, ``$output`` collection.  Those are the parts a test is
supposed to be testing, so they are deliberately not stubbable.

Stubs are answered *after* prompt interpolation and *before* output parsing, which
means a stub is validated against the node's declared output contract by the same
code that validates a real model response — no separate schema check to drift.

    from flow.testing import load_suite, run_case

    suite, wf = load_suite(Path("release_readiness.test.json"))
    for case in suite.cases:
        result = run_case(wf, case, base_dir=suite.workflow_path.parent)
"""

from __future__ import annotations

from flow.testing.case import CaseResult, Failure, run_case, run_case_async
from flow.testing.loader import TEST_SUFFIX, discover, load_suite, suite_path_for
from flow.testing.match import first_difference, matches
from flow.testing.models import Case, Expect, NodeStub, StubRule, Suite
from flow.testing.stubs import CaseStubs, StubMiss

__all__ = [
    "TEST_SUFFIX",
    "Case",
    "CaseResult",
    "CaseStubs",
    "Expect",
    "Failure",
    "NodeStub",
    "StubMiss",
    "StubRule",
    "Suite",
    "discover",
    "first_difference",
    "load_suite",
    "matches",
    "run_case",
    "run_case_async",
    "suite_path_for",
]
