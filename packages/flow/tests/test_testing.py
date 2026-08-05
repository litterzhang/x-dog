"""Workflow test suites — ``xdog-flow test`` and the ``flow.testing`` machinery.

A suite stubs only the non-deterministic boundaries (agent turns, human signals,
and opt-in script nodes); edges, conditions, loops, fan-out, mappings, coercion and
``$output`` collection all run for real, because those are what a workflow test is
supposed to be testing.

The properties worth pinning down here are the ones that make a suite trustworthy:
stub selection is deterministic under concurrency, a stub is validated by the same
code that validates a real model response, and every authoring mistake is caught
before anything executes.  See ``docs/testing.md``.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from flow.errors import WorkflowValidationError
from flow.loader import parse_workflow
from flow.testing import load_suite, run_case
from flow.testing.match import first_difference, matches


# --------------------------------------------------------------------------
# Fixtures: small workflows that isolate one graph feature each.
# --------------------------------------------------------------------------
def _fan_workflow() -> dict[str, Any]:
    """``$in -> plan -> (fan) worker -> total -> $output``."""
    return {
        "name": "fan",
        # Inert: every agent turn is answered by a stub, so no provider is ever built.
        "provider": "copilot",
        "defaults": {"model": "stub-model"},
        "entry": "plan",
        "in_schema": {"topic": {"type": "string"}},
        "state": {"topic": ""},
        "nodes": [
            {
                "id": "plan",
                "type": "agent",
                "inputs": ["topic"],
                "prompt": "Plan for {{$.topic}}",
                "outputs": [
                    {"name": "items", "schema": {"type": "array", "items": {"type": "object"}}},
                    {"name": "note", "schema": {"type": "string"}},
                ],
            },
            {
                "id": "worker",
                "type": "agent",
                "inputs": [{"name": "item", "schema": {"type": "object"}}],
                "prompt": "Work on {{$.item}}",
                "outputs": [{"name": "score", "schema": {"type": "integer"}}],
            },
            {
                "id": "total",
                "type": "script",
                "inputs": [{"name": "scores", "schema": {"type": "array", "items": {"type": "integer"}}}],
                "outputs": [{"name": "sum", "schema": {"type": "integer"}}],
                "code": "def total(ctx, scores):\n    return sum(scores)\n",
            },
        ],
        "edges": [
            {"from": "$in", "to": "plan", "map": {"topic": "topic"}},
            {"from": "plan", "to": "worker", "fan_out": "items", "map": {"items": "item"}},
            {"from": "worker", "to": "total", "fan_in": "list", "map": {"score": "scores"}},
            {"from": "total", "to": "$output", "map": {"sum": "sum"}},
        ],
    }


def _loop_workflow() -> dict[str, Any]:
    """``draft -> review -> (loop, max 2) draft`` with a quality gate."""
    return {
        "name": "loop",
        # Inert: every agent turn is answered by a stub, so no provider is ever built.
        "provider": "copilot",
        "defaults": {"model": "stub-model"},
        "entry": "draft",
        "in_schema": {"topic": {"type": "string"}},
        "state": {"topic": ""},
        "nodes": [
            {
                "id": "draft",
                "type": "agent",
                "inputs": ["topic"],
                "prompt": "Draft {{$.topic}}",
                "outputs": ["text"],
            },
            {
                "id": "review",
                "type": "agent",
                "inputs": [{"name": "text", "schema": {"type": "string"}}],
                "prompt": "Review {{$.text}}",
                "outputs": [
                    {"name": "text", "schema": {"type": "string"}},
                    {"name": "score", "schema": {"type": "integer"}},
                ],
            },
        ],
        "edges": [
            {"from": "$in", "to": "draft", "map": {"topic": "topic"}},
            {"from": "draft", "to": "review", "map": {"text": "text"}},
            {
                "from": "review",
                "to": "draft",
                "when": {"lt": {"value": "{{$.score}}", "text": "8"}},
                "loop": {"max": 2},
                "map": {"text": "topic"},
            },
            {"from": "review", "to": "$output", "map": {"text": "text", "score": "score"}},
        ],
    }


def _human_workflow() -> dict[str, Any]:
    """``propose -> approve (human) -> publish``."""
    return {
        "name": "human",
        # Inert: every agent turn is answered by a stub, so no provider is ever built.
        "provider": "copilot",
        "defaults": {"model": "stub-model"},
        "entry": "propose",
        "in_schema": {"topic": {"type": "string"}},
        "state": {"topic": ""},
        "nodes": [
            {"id": "propose", "type": "agent", "inputs": ["topic"], "prompt": "{{$.topic}}", "outputs": ["plan"]},
            {
                "id": "approve",
                "type": "human",
                "signal": "go",
                "inputs": [{"name": "plan", "schema": {"type": "string"}}],
                "outputs": [{"name": "plan", "schema": {"type": "string"}}],
            },
            {
                "id": "publish",
                "type": "script",
                "inputs": [{"name": "plan", "schema": {"type": "string"}}],
                "outputs": [{"name": "url", "schema": {"type": "string"}}],
                "code": "def publish(ctx, plan):\n    return 'https://example/' + plan\n",
            },
        ],
        "edges": [
            {"from": "$in", "to": "propose", "map": {"topic": "topic"}},
            {"from": "propose", "to": "approve", "map": {"plan": "plan"}},
            {"from": "approve", "to": "publish", "map": {"plan": "plan"}},
            {"from": "publish", "to": "$output", "map": {"url": "url"}},
        ],
    }


def _write(tmp_path: pathlib.Path, workflow: dict[str, Any], suite: dict[str, Any]) -> pathlib.Path:
    """Write a workflow + its companion suite; return the workflow path."""
    wf_path = tmp_path / f"{workflow['name']}.json"
    wf_path.write_text(json.dumps(workflow), encoding="utf-8")
    (tmp_path / f"{workflow['name']}.test.json").write_text(json.dumps(suite), encoding="utf-8")
    return wf_path


def _run_one(tmp_path: pathlib.Path, workflow: dict[str, Any], case: dict[str, Any], **kw: Any) -> Any:
    wf_path = _write(tmp_path, workflow, {"cases": [case]})
    suite, wf = load_suite(wf_path, **kw)
    return run_case(wf, suite.cases[0], base_dir=wf_path.parent)


# --------------------------------------------------------------------------
# The matcher — one rule shared by `when` and `expect.output`.
# --------------------------------------------------------------------------
def test_object_match_is_a_subset_but_array_match_is_exact() -> None:
    # Extra object keys are ignored: a case asserts intent, not full structure.
    assert matches({"a": 1}, {"a": 1, "b": 2})
    assert not matches({"a": 1, "b": 2}, {"a": 1})
    # Arrays compare by length too — "how many items came out of the fan" is
    # usually the thing under test, so a short pattern must not silently pass.
    assert matches([1, 2], [1, 2])
    assert not matches([1], [1, 2])
    assert matches({"x": [{"k": 1}]}, {"x": [{"k": 1, "extra": 0}]})


def test_bool_never_matches_a_number() -> None:
    # Python's True == 1 would otherwise let `"flag": 1` pass against `True`.
    assert not matches(True, 1)
    assert not matches(1, True)
    assert matches(True, True)


def test_first_difference_names_the_deepest_path() -> None:
    diff = first_difference({"risk": {"status": "blocked"}}, {"risk": {"status": "warn"}}, "expect.output")
    assert diff == ("expect.output.risk.status", "blocked", "warn")


# --------------------------------------------------------------------------
# Fan-out: `when` matches inputs, `index` matches array position.
# --------------------------------------------------------------------------
def test_fan_out_stubs_select_by_input_value(tmp_path: pathlib.Path) -> None:
    result = _run_one(
        tmp_path,
        _fan_workflow(),
        {
            "name": "by value",
            "inputs": {"topic": "t"},
            "agents": {
                "plan": {
                    "items": [{"kind": "a"}, {"kind": "b"}, {"kind": "a"}],
                    "note": "n",
                },
                "worker": [
                    {"when": {"item": {"kind": "a"}}, "then": {"score": 10}},
                    {"then": {"score": 1}},
                ],
            },
            "expect": {"output": {"sum": 21}, "calls": {"worker": 3, "total": 1}},
        },
    )
    assert result.ok, result.failures


def test_fan_out_stubs_select_by_array_index(tmp_path: pathlib.Path) -> None:
    """``index`` is the position in the fanned array, not completion order.

    Instances run concurrently, so a selector keyed on arrival order would make the
    case flaky; keying on the source array keeps it deterministic.
    """
    result = _run_one(
        tmp_path,
        _fan_workflow(),
        {
            "name": "by index",
            "inputs": {"topic": "t"},
            "agents": {
                "plan": {"items": [{}, {}, {}], "note": "n"},
                "worker": [
                    {"index": 0, "then": {"score": 100}},
                    {"index": 1, "then": {"score": 20}},
                    {"then": {"score": 3}},
                ],
            },
            "expect": {"output": {"sum": 123}, "calls": {"worker": 3}},
        },
    )
    assert result.ok, result.failures


def test_calls_counts_fan_instances_not_activations(tmp_path: pathlib.Path) -> None:
    """A fan group is one activation but N invocations; ``calls`` reports N.

    The trace cannot express this (a fan contributes a single frame), which is why
    counting rides on ``NodeFinished.instances``.
    """
    result = _run_one(
        tmp_path,
        _fan_workflow(),
        {
            "name": "counts",
            "inputs": {"topic": "t"},
            "agents": {
                "plan": {"items": [{}, {}, {}, {}], "note": "n"},
                "worker": {"score": 1},
            },
            "expect": {"calls": {"worker": 4}},
        },
    )
    assert result.ok, result.failures
    assert result.calls["worker"] == 4
    assert result.rounds["worker"] == 1


# --------------------------------------------------------------------------
# Loops: `round` is the activation ordinal, and the bound is observable.
# --------------------------------------------------------------------------
def test_loop_round_selector_drives_termination(tmp_path: pathlib.Path) -> None:
    result = _run_one(
        tmp_path,
        _loop_workflow(),
        {
            "name": "one revision",
            "inputs": {"topic": "t"},
            "agents": {
                "draft": {"text": "d"},
                "review": [
                    {"round": 1, "then": {"text": "v1", "score": 5}},
                    {"then": {"text": "v2", "score": 9}},
                ],
            },
            "expect": {
                "output": {"text": "v2", "score": 9},
                "calls": {"draft": 2, "review": 2},
            },
        },
    )
    assert result.ok, result.failures


def test_loop_max_bounds_the_back_edge(tmp_path: pathlib.Path) -> None:
    """``loop.max`` counts back-edge firings, so max=2 yields 3 reviews."""
    result = _run_one(
        tmp_path,
        _loop_workflow(),
        {
            "name": "never satisfied",
            "inputs": {"topic": "t"},
            "agents": {"draft": {"text": "d"}, "review": {"text": "v", "score": 1}},
            "expect": {"calls": {"draft": 3, "review": 3}},
        },
    )
    assert result.ok, result.failures


# --------------------------------------------------------------------------
# Outcomes: success / error / paused are a closed three-way choice.
# --------------------------------------------------------------------------
def test_human_node_pauses_without_its_signal(tmp_path: pathlib.Path) -> None:
    result = _run_one(
        tmp_path,
        _human_workflow(),
        {
            "name": "pauses",
            "inputs": {"topic": "t"},
            "agents": {"propose": {"plan": "p"}},
            "expect": {"paused": "approve", "calls": {"publish": 0}},
        },
    )
    assert result.ok, result.failures


def test_human_node_proceeds_once_signalled(tmp_path: pathlib.Path) -> None:
    # A signalled human node emits the literal "approved" into its output port
    # (it is a gate, not a passthrough), and the downstream script then runs for real.
    result = _run_one(
        tmp_path,
        _human_workflow(),
        {
            "name": "proceeds",
            "inputs": {"topic": "t"},
            "signals": ["go"],
            "agents": {"propose": {"plan": "p"}},
            "expect": {"output": {"url": "https://example/approved"}, "calls": {"publish": 1}},
        },
    )
    assert result.ok, result.failures


def test_expect_error_matches_a_message_substring(tmp_path: pathlib.Path) -> None:
    workflow = _fan_workflow()
    workflow["nodes"][2]["code"] = "def total(ctx, scores):\n    raise ValueError('boom in total')\n"
    result = _run_one(
        tmp_path,
        workflow,
        {
            "name": "script blows up",
            "inputs": {"topic": "t"},
            "agents": {"plan": {"items": [{}], "note": "n"}, "worker": {"score": 1}},
            "expect": {"error": "boom in total"},
        },
    )
    assert result.ok, result.failures


def test_unexpected_failure_is_reported_not_swallowed(tmp_path: pathlib.Path) -> None:
    workflow = _fan_workflow()
    workflow["nodes"][2]["code"] = "def total(ctx, scores):\n    raise ValueError('boom')\n"
    result = _run_one(
        tmp_path,
        workflow,
        {
            "name": "expects success",
            "inputs": {"topic": "t"},
            "agents": {"plan": {"items": [{}], "note": "n"}, "worker": {"score": 1}},
            "expect": {"output": {"sum": 1}},
        },
    )
    assert not result.ok
    assert any(f.where == "expect.success" for f in result.failures)


# --------------------------------------------------------------------------
# Stub integrity: a stub is validated by the production path, and a stale
# selector is a failure rather than a silent fall-through.
# --------------------------------------------------------------------------
def test_stub_output_is_validated_by_the_nodes_own_contract(tmp_path: pathlib.Path) -> None:
    """A multi-port agent stub missing a field fails exactly as a real reply would.

    Nothing in flow.testing checks this — the node's own required-field check does,
    which is the point of stubbing at the provider seam.
    """
    result = _run_one(
        tmp_path,
        _fan_workflow(),
        {
            "name": "incomplete stub",
            "inputs": {"topic": "t"},
            "agents": {"plan": {"items": [{}]}, "worker": {"score": 1}},
            "expect": {"output": {"sum": 1}},
        },
    )
    assert not result.ok
    assert "missing field 'note'" in result.error


def test_no_matching_rule_fails_loudly_with_the_actual_inputs(tmp_path: pathlib.Path) -> None:
    result = _run_one(
        tmp_path,
        _fan_workflow(),
        {
            "name": "unmatched call",
            "inputs": {"topic": "t"},
            "agents": {
                "plan": {"items": [{"kind": "z"}], "note": "n"},
                "worker": [{"when": {"item": {"kind": "a"}}, "then": {"score": 1}}],
            },
            "expect": {"output": {"sum": 1}},
        },
    )
    assert not result.ok
    assert "no stub rule matched" in result.error
    assert "'kind': 'z'" in result.error


def test_a_selector_that_never_fires_is_a_failure(tmp_path: pathlib.Path) -> None:
    """A stale selector would otherwise fall through and quietly assert nothing."""
    result = _run_one(
        tmp_path,
        _loop_workflow(),
        {
            "name": "stale round",
            "inputs": {"topic": "t"},
            "agents": {
                "draft": {"text": "d"},
                "review": [
                    {"round": 9, "then": {"text": "never", "score": 0}},
                    {"then": {"text": "v", "score": 9}},
                ],
            },
            "expect": {"calls": {"review": 1}},
        },
    )
    assert not result.ok
    assert any("never matched" in f.actual for f in result.failures)


def test_a_stub_for_a_branch_the_case_skips_is_fine(tmp_path: pathlib.Path) -> None:
    """Nodes that never ran are exempt, so per-branch stubs are not noise."""
    result = _run_one(
        tmp_path,
        _human_workflow(),
        {
            "name": "unused stub",
            "inputs": {"topic": "t"},
            "agents": {"propose": {"plan": "p"}},
            "scripts": {"publish": {"url": "never-used"}},
            "expect": {"paused": "approve"},
        },
        allow_script_stub=True,
    )
    assert result.ok, result.failures


# --------------------------------------------------------------------------
# Load-time validation: authoring mistakes never reach execution.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("case_patch", "expected"),
    [
        ({"agents": {"nope": {"text": "x"}}}, "no such node"),
        ({"agents": {"total": {"sum": 1}}}, "is a script node"),
        ({"agents": {"worker": {"bogus": 1}}}, "has no output port"),
        ({"expect": {"calls": {"ghost": 1}}}, "no such node"),
        ({"expect": {"paused": "worker"}}, "must name a human node"),
        ({"expect": {"success": False}}, "instead of success=false"),
        ({"expect": {"success": True, "error": "x"}}, "mutually exclusive"),
        ({"agents": {"worker": [{"then": {"score": 1}}, {"index": 0, "then": {"score": 2}}]}}, "must be last"),
        ({"agents": {"worker": [{"index": 0}]}}, "missing 'then'"),
        ({"typo": 1}, "unknown key"),
    ],
)
def test_authoring_mistakes_are_rejected_before_running(
    tmp_path: pathlib.Path, case_patch: dict[str, Any], expected: str
) -> None:
    case: dict[str, Any] = {
        "name": "c",
        "agents": {"plan": {"items": [], "note": "n"}, "worker": {"score": 1}},
        "expect": {},
    }
    case.update(case_patch)
    wf_path = _write(tmp_path, _fan_workflow(), {"cases": [case]})
    with pytest.raises(WorkflowValidationError, match=expected):
        load_suite(wf_path)


def test_script_stubs_require_an_explicit_opt_in(tmp_path: pathlib.Path) -> None:
    """Stubbing deterministic logic hides the thing under test, so it is opt-in."""
    wf_path = _write(
        tmp_path,
        _fan_workflow(),
        {
            "cases": [
                {
                    "name": "c",
                    "agents": {"plan": {"items": [], "note": "n"}, "worker": {"score": 1}},
                    "scripts": {"total": {"sum": 0}},
                    "expect": {},
                }
            ]
        },
    )
    with pytest.raises(WorkflowValidationError, match="--allow-script-stub"):
        load_suite(wf_path)
    suite, _ = load_suite(wf_path, allow_script_stub=True)
    assert suite.cases[0].scripts["total"].rules[0].then == {"sum": 0}


def test_an_unstubbed_agent_node_never_reaches_a_provider(tmp_path: pathlib.Path) -> None:
    """The stub runner answers every agent node, so a gap fails instead of dialling out."""
    result = _run_one(
        tmp_path,
        _fan_workflow(),
        {
            "name": "missing stub",
            "inputs": {"topic": "t"},
            "agents": {"plan": {"items": [{}], "note": "n"}},
            "expect": {"output": {"sum": 1}},
        },
    )
    assert not result.ok
    assert "has no stub" in result.error


# --------------------------------------------------------------------------
# Discovery.
# --------------------------------------------------------------------------
def test_suite_is_found_from_the_workflow_path(tmp_path: pathlib.Path) -> None:
    wf_path = _write(
        tmp_path,
        _fan_workflow(),
        {"cases": [{"name": "c", "agents": {"plan": {"items": [], "note": "n"}, "worker": {"score": 1}}}]},
    )
    from_workflow, _ = load_suite(wf_path)
    from_suite, _ = load_suite(tmp_path / "fan.test.json")
    assert from_workflow.path == from_suite.path
    assert from_workflow.workflow_path.name == "fan.json"


def test_missing_suite_is_a_clear_error(tmp_path: pathlib.Path) -> None:
    (tmp_path / "solo.json").write_text(json.dumps(_fan_workflow()), encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="no test suite at"):
        from flow.testing import discover

        discover(tmp_path / "solo.json")


def test_shipped_examples_have_passing_suites() -> None:
    """The flagship examples are covered by their own suites."""
    examples = pathlib.Path(__file__).resolve().parent.parent / "examples"
    for suite_file in sorted(examples.glob("*.test.json")):
        suite, wf = load_suite(suite_file, allow_script_stub=True)
        assert suite.cases, suite_file
        for case in suite.cases:
            result = run_case(wf, case, base_dir=suite.workflow_path.parent)
            assert result.ok, f"{suite_file.name} / {case.name}: {result.failures} {result.error}"


def test_workflow_fixtures_still_parse() -> None:
    """Guard the helper workflows above against loader drift."""
    for builder in (_fan_workflow, _loop_workflow, _human_workflow):
        assert parse_workflow(builder()).nodes
