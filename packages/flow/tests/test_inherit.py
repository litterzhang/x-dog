"""`inherit` — validation, which is where this feature earns its keep.

A session crossing a node boundary is data, and flow's promise is that data
moves only along things the graph can see. So the reference is checked before
anything runs. The split that matters: **strict here, lenient at run time**. A
missing session is tolerated when the run happens — the first pass of a
self-inheriting loop has none, and a skipped branch never makes one — so without
a load-time check a typo in `from` would do nothing at all and never say so.
"""
from __future__ import annotations

import pytest
from xdog.flow.errors import WorkflowValidationError
from xdog.flow.loader import parse_workflow, validate_workflow


def _wf(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {
        "name": "inh",
        "provider": "copilot",
        "defaults": {"model": "m"},
        "entry": nodes[0]["id"],
        "nodes": nodes,
        "edges": edges
        or [{"from": nodes[-1]["id"], "to": "$output", "map": {"out": "result"}}],
    }


def _agent(node_id: str, **extra: object) -> dict:
    node = {"id": node_id, "type": "agent", "prompt": "go", "outputs": ["out"], **extra}
    node.setdefault("inputs", [{"name": "seed", "schema": {"type": "string"}, "required": False}])
    return node


def _check(doc: dict) -> None:
    validate_workflow(parse_workflow(doc))


# -- The reference itself ----------------------------------------------------


def test_inheriting_from_an_earlier_agent_is_accepted() -> None:
    _check(_wf(
        [_agent("a"), _agent("b", inherit={"from": "a"})],
        [{"from": "a", "to": "b", "map": {"out": "seed"}},
         {"from": "b", "to": "$output", "map": {"out": "result"}}],
    ))


def test_a_node_may_inherit_from_itself() -> None:
    """The loop case: a node keeps its own context across iterations instead of
    restarting cold on every pass."""
    _check(_wf(
        [_agent("a"), _agent("b", inherit={"from": "b"})],
        [{"from": "a", "to": "b", "map": {"out": "seed"}},
         {"from": "b", "to": "$output", "map": {"out": "result"}}],
    ))


def test_an_unknown_source_is_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="not found in nodes"):
        _check(_wf([_agent("a", inherit={"from": "nope"})]))


def test_inheriting_from_a_script_node_is_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="has no agent session"):
        _check(_wf(
            [{"id": "s", "type": "script", "code": "def s(ctx):\n    return 1",
              "outputs": ["out"]},
             _agent("b", inherit={"from": "s"})],
            [{"from": "s", "to": "b", "map": {"out": "seed"}},
             {"from": "b", "to": "$output", "map": {"out": "result"}}],
        ))


# -- The traps: each fails silently without a rule ---------------------------


def test_inheriting_from_a_deterministic_node_is_rejected() -> None:
    """A memo hit returns stored ports *without running the node*, so the source
    can never produce a session at all. Distinct from absent-on-first-pass,
    which is forgiven — this one can never appear."""
    with pytest.raises(WorkflowValidationError, match="which is deterministic"):
        _check(_wf(
            [_agent("a", deterministic=True), _agent("b", inherit={"from": "a"})],
            [{"from": "a", "to": "b", "map": {"out": "seed"}},
             {"from": "b", "to": "$output", "map": {"out": "result"}}],
        ))


def test_inheriting_from_a_fan_out_worker_is_rejected() -> None:
    """N instances run under one node id with no per-instance identity, so
    "the" session is ambiguous."""
    with pytest.raises(WorkflowValidationError, match="fan-out worker"):
        _check(_wf(
            [_agent("a", outputs=[{"name": "items", "schema": {"type": "array"}}]),
             _agent("w"),
             _agent("c", inherit={"from": "w"})],
            [{"from": "a", "to": "w", "fan_out": "items", "map": {"items": "item"}},
             {"from": "w", "to": "c", "map": {"out": "seed"}},
             {"from": "c", "to": "$output", "map": {"out": "result"}}],
        ))


def test_a_cli_backend_cannot_inherit_or_be_inherited_from() -> None:
    """A CLI agent owns its own session; flow can neither read nor seed it."""
    with pytest.raises(WorkflowValidationError, match="in-process SDK"):
        _check(_wf(
            [_agent("a", backend="claude-cli"), _agent("b", inherit={"from": "a"})],
            [{"from": "a", "to": "b", "map": {"out": "seed"}},
             {"from": "b", "to": "$output", "map": {"out": "result"}}],
        ))

    with pytest.raises(WorkflowValidationError, match="in-process SDK"):
        _check(_wf(
            [_agent("a"), _agent("b", backend="claude-cli", inherit={"from": "a"})],
            [{"from": "a", "to": "b", "map": {"out": "seed"}},
             {"from": "b", "to": "$output", "map": {"out": "result"}}],
        ))


# -- Ordering ----------------------------------------------------------------


def test_inheriting_from_a_later_node_is_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="not declared before it"):
        _check(_wf(
            [_agent("a", inherit={"from": "b"}), _agent("b")],
            [{"from": "a", "to": "b", "map": {"out": "seed"}},
             {"from": "b", "to": "$output", "map": {"out": "result"}}],
        ))


def test_inheriting_from_a_conditional_branch_is_rejected() -> None:
    """A `when`-gated source may never run. Tolerable at run time, but naming
    one is almost always an authoring mistake, so it is refused at load."""
    with pytest.raises(WorkflowValidationError, match="not guaranteed to run first"):
        _check(_wf(
            [_agent("a"), _agent("b"), _agent("c", inherit={"from": "b"})],
            [{"from": "a", "to": "b", "map": {"out": "seed"},
              "when": {"contains": {"value": "{{$.out}}", "text": "x"}}},
             {"from": "a", "to": "c", "map": {"out": "seed"}},
             {"from": "c", "to": "$output", "map": {"out": "result"}}],
        ))


# -- Shape -------------------------------------------------------------------


def test_inherit_must_be_an_object_with_only_from() -> None:
    with pytest.raises(WorkflowValidationError, match="must be an object"):
        _check(_wf([_agent("a", inherit="b")]))

    with pytest.raises(WorkflowValidationError, match="unknown 'inherit' keys"):
        _check(_wf([_agent("a"), _agent("b", inherit={"from": "a", "tools": True})],
                   [{"from": "a", "to": "b", "map": {"out": "seed"}},
                    {"from": "b", "to": "$output", "map": {"out": "result"}}]))

    with pytest.raises(WorkflowValidationError, match="non-empty node id"):
        _check(_wf([_agent("a", inherit={"from": ""})]))
