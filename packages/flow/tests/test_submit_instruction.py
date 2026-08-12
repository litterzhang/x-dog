"""The submit_result instruction must agree with the schema it is validated against.

`agent_output_schema` and the system-prompt line that tells a model what to
submit are two halves of one contract, and they disagreed for every node whose
single output port is not a plain string. The instruction said "an object
containing these fields: x"; the schema for a single structured port is that
port's OWN schema. So a node with one `array` port asked for `{"x": [...]}`
and validated against `{"type": "array"}`.

The model obeyed the prompt, validation rejected the object, submit_result
returned an error string, the sink stayed empty, and the run ended with
"agent did not submit a result via submit_result" -- an error that reads like
the model ignored its instructions when it had followed them exactly.

No stub suite could see this. A stub IS the submitted result: it supplies the
value this instruction exists to elicit, so the instruction is never read by
anything. It shows up only against a real model.
"""

from __future__ import annotations

import json

import pytest
from xdog.flow.loader import parse_workflow
from xdog.flow.models import (
    agent_is_structured,
    agent_output_schema,
    submit_instruction,
)


def node_with(outputs):
    wf = parse_workflow(
        {
            "name": "t",
            "provider": "copilot",
            "entry": "a",
            "state": {"x": ""},
            "nodes": [
                {
                    "id": "a",
                    "type": "agent",
                    "inputs": [{"name": "x", "schema": {"type": "string"}}],
                    "prompt": "go",
                    "outputs": outputs,
                }
            ],
            "edges": [{"from": "$in", "to": "a", "map": {"x": "x"}}],
        }
    )
    return next(n for n in wf.nodes if n.id == "a")


ARRAY = {"name": "items", "schema": {"type": "array", "items": {"type": "string"}}}
OBJECT = {"name": "blob", "schema": {"type": "object"}}
STRING = {"name": "text", "schema": {"type": "string"}}
NUMBER = {"name": "n", "schema": {"type": "number"}}


@pytest.mark.parametrize("port", [ARRAY, OBJECT, NUMBER], ids=lambda p: p["name"])
def test_a_single_structured_port_is_told_to_submit_the_bare_value(port):
    """The schema for one structured port IS that port's schema.

    Telling the model to wrap it in an object guarantees a validation failure.
    """
    node = node_with([port])
    assert agent_is_structured(node)
    schema = agent_output_schema(node)
    instruction = submit_instruction(node)

    # The schema expects the value itself...
    assert schema["type"] == port["schema"]["type"]
    assert "properties" not in schema
    # ...so the instruction must not ask for a wrapper.
    assert "Do not wrap it in an object" in instruction
    assert "object containing these fields" not in instruction
    assert port["name"] in instruction


def test_multiple_ports_are_still_told_to_submit_an_object():
    node = node_with([ARRAY, STRING])
    schema = agent_output_schema(node)
    instruction = submit_instruction(node)

    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"items", "text"}
    assert "object containing these fields" in instruction
    assert "items" in instruction and "text" in instruction


def test_a_lone_string_port_is_not_structured_at_all():
    """Its reply text goes verbatim into the port; no tool involved."""
    assert not agent_is_structured(node_with([STRING]))


@pytest.mark.parametrize(
    "outputs",
    [[ARRAY], [OBJECT], [NUMBER], [ARRAY, STRING], [STRING, NUMBER]],
    ids=["array", "object", "number", "array+string", "string+number"],
)
def test_the_instruction_describes_a_value_the_schema_accepts(outputs):
    """The property this file exists for, stated once.

    Whatever shape the instruction asks for, the schema must accept it. This is
    checked by building the value the instruction describes and validating it
    the way submit_result does.
    """
    import fastjsonschema

    node = node_with(outputs)
    schema = agent_output_schema(node)
    instruction = submit_instruction(node)

    sample_for = {
        "array": ["a"],
        "object": {"k": "v"},
        "number": 1,
        "string": "s",
    }
    if len(outputs) == 1:
        described = sample_for[outputs[0]["schema"]["type"]]
    else:
        described = {o["name"]: sample_for[o["schema"]["type"]] for o in outputs}

    # Would have raised for a single array port before the fix.
    fastjsonschema.compile(schema)(described)

    wraps = "object containing these fields" in instruction
    assert wraps == (len(outputs) > 1), (
        f"instruction and schema disagree for {json.dumps(outputs)}"
    )
