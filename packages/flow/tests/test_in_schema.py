"""#2/#3 — the workflow's typed input/output signature.

``$in`` seeds are untyped by default; a workflow may OPT IN by declaring
``in_schema`` (name -> JSON Schema), which then type-checks edges out of ``$in``
and lets a typed array seed drive a ``fan_out``.  ``workflow_output_schema``
derives the workflow's output signature statically from the ``$output`` edges.

All of this is load-time only — no runtime change, ``interpret == compile``
untouched.
"""

from __future__ import annotations

from typing import Any

import pytest
from flow.builder.serialize import workflow_to_dict
from flow.errors import WorkflowValidationError
from flow.loader import (
    parse_workflow,
    validate_workflow,
    workflow_input_schema,
    workflow_output_schema,
)


def _wf(in_schema: dict[str, Any] | None = None, topic_port_type: str = "string") -> dict[str, Any]:
    """A one-agent workflow: $in.topic -> agent -> $output.result."""
    d: dict[str, Any] = {
        "name": "sig",
        "provider": "copilot",
        "entry": "a",
        "state": {"topic": "hi"},
        "nodes": [
            {
                "id": "a",
                "type": "agent",
                "prompt": "{{topic}}",
                "inputs": [{"name": "topic", "type": topic_port_type}],
                "outputs": ["y"],
            }
        ],
        "edges": [
            {"from": "$in", "to": "a", "map": {"topic": "topic"}},
            {"from": "a", "to": "$output", "map": {"y": "result"}},
        ],
    }
    if in_schema is not None:
        d["in_schema"] = in_schema
    return d


# --- #3: opt-in $in typing -------------------------------------------------


def test_undeclared_in_stays_untyped() -> None:
    """No in_schema: a string seed feeding an integer port is still exempt (compat)."""
    validate_workflow(parse_workflow(_wf(topic_port_type="integer")))  # no raise


def test_declared_in_schema_catches_type_mismatch() -> None:
    """A declared string $in feeding an integer port fails at load."""
    d = _wf(in_schema={"topic": {"type": "string"}}, topic_port_type="integer")
    with pytest.raises(WorkflowValidationError, match="type mismatch"):
        validate_workflow(parse_workflow(d))


def test_declared_in_schema_matching_type_ok() -> None:
    """A declared string $in feeding a string port validates."""
    d = _wf(in_schema={"topic": {"type": "string"}}, topic_port_type="string")
    validate_workflow(parse_workflow(d))  # no raise


def test_in_schema_must_be_object() -> None:
    d = _wf()
    d["in_schema"] = {"topic": "not-a-schema"}
    with pytest.raises(WorkflowValidationError, match="must be a JSON Schema object"):
        parse_workflow(d)


def _fanout_from_in(with_schema: bool) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": "fin",
        "provider": "copilot",
        "entry": "w",
        "state": {"items": ["a", "b", "c"]},
        "nodes": [
            {"id": "w", "type": "agent", "prompt": "{{it}}", "inputs": ["it"], "outputs": ["r"]},
            {
                "id": "m",
                "type": "script",
                "inputs": [{"name": "rs", "schema": {"type": "array"}}],
                "code": "def m(ctx, rs):\n    return len(rs)",
                "outputs": ["c"],
            },
        ],
        "edges": [
            {"from": "$in", "to": "w", "fan_out": "items", "map": {"items": "it"}},
            {"from": "w", "to": "m", "fan_in": "list", "map": {"r": "rs"}},
            {"from": "m", "to": "$output", "map": {"c": "count"}},
        ],
    }
    if with_schema:
        d["in_schema"] = {"items": {"type": "array", "items": {"type": "string"}}}
    return d


def test_fan_out_from_typed_in_array_ok() -> None:
    """With an array in_schema, $in can drive a fan_out (previously always rejected)."""
    validate_workflow(parse_workflow(_fanout_from_in(with_schema=True)))  # no raise


def test_fan_out_from_untyped_in_still_rejected() -> None:
    """Without in_schema, $in is untyped and cannot drive a fan_out."""
    with pytest.raises(WorkflowValidationError, match="untyped seed cannot drive fan-out"):
        validate_workflow(parse_workflow(_fanout_from_in(with_schema=False)))


def test_in_schema_roundtrips() -> None:
    wf = parse_workflow(_fanout_from_in(with_schema=True))
    assert dict(wf.in_schema) == {"items": {"type": "array", "items": {"type": "string"}}}
    assert parse_workflow(workflow_to_dict(wf)) == wf


def test_workflow_input_schema_signature() -> None:
    wf = parse_workflow(_fanout_from_in(with_schema=True))
    sig = workflow_input_schema(wf)
    assert sig["type"] == "object"
    assert sig["properties"] == {"items": {"type": "array", "items": {"type": "string"}}}
    assert sig["required"] == ["items"]


def test_workflow_input_schema_empty_when_undeclared() -> None:
    wf = parse_workflow(_wf())
    assert workflow_input_schema(wf) == {"type": "object", "properties": {}, "required": []}


# --- #2: output schema derivation ------------------------------------------


def test_output_schema_from_structured_port() -> None:
    """$output.result takes the schema of the source node's structured port."""
    d = {
        "name": "osc",
        "provider": "copilot",
        "entry": "a",
        "nodes": [
            {
                "id": "a",
                "type": "agent",
                "prompt": "p",
                "outputs": [{"name": "plan", "schema": {"type": "object", "properties": {"k": {"type": "string"}}}}],
            }
        ],
        "edges": [{"from": "a", "to": "$output", "map": {"plan": "result"}}],
    }
    osc = workflow_output_schema(parse_workflow(d))
    assert osc["type"] == "object"
    assert osc["properties"]["result"] == {"type": "object", "properties": {"k": {"type": "string"}}}
    assert osc["required"] == ["result"]


def test_output_schema_scalar_port() -> None:
    """A plain string output port yields a string schema in the signature."""
    d = {
        "name": "osc2",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "p", "outputs": ["y"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"y": "result"}}],
    }
    osc = workflow_output_schema(parse_workflow(d))
    assert osc["properties"]["result"] == {"type": "string"}


def test_output_schema_multiple_keys() -> None:
    d = {
        "name": "osc3",
        "provider": "copilot",
        "entry": "a",
        "nodes": [
            {"id": "a", "type": "script", "code": "def a(ctx):\n    return {'x': 1, 'y': 2}",
             "outputs": [{"name": "x", "type": "integer"}, {"name": "y", "type": "integer"}]},
        ],
        "edges": [
            {"from": "a", "to": "$output", "map": {"x": "first"}},
            {"from": "a", "to": "$output", "map": {"y": "second"}},
        ],
    }
    osc = workflow_output_schema(parse_workflow(d))
    assert set(osc["properties"]) == {"first", "second"}
    assert osc["properties"]["first"] == {"type": "integer"}
    assert osc["required"] == ["first", "second"]
