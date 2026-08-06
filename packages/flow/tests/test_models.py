"""Tests for flow.models — construction and immutability checks."""

from __future__ import annotations

import dataclasses

import pytest
from xdog.flow.models import Condition, EdgeDef, NodeDef, Port, WorkflowDef


def test_node_def_defaults() -> None:
    node = NodeDef(id="n1")
    assert node.id == "n1"
    assert node.type == "agent"
    assert node.model is None
    assert node.system_prompt == ""
    assert node.prompt == ""
    assert node.output_ports == ()
    assert node.output_names == ()


def test_condition_defaults() -> None:
    cond = Condition(op="equals")
    assert cond.op == "equals"
    assert cond.value is None
    assert cond.text is None
    assert cond.children == ()


def test_edge_def_defaults() -> None:
    edge = EdgeDef(src="a", dst="b")
    assert edge.src == "a"
    assert edge.dst == "b"
    assert edge.when is None
    assert edge.loop_max is None


def test_workflow_def_construction() -> None:
    node = NodeDef(id="start", prompt="go")
    edge = EdgeDef(src="start", dst="end")
    wf = WorkflowDef(
        name="my-flow",
        provider="anthropic",
        entry="start",
        nodes=(node,),
        edges=(edge,),
    )
    assert wf.name == "my-flow"
    assert wf.provider == "anthropic"
    assert wf.entry == "start"
    assert len(wf.nodes) == 1
    assert len(wf.edges) == 1
    assert wf.default_model == ""
    assert wf.initial_state == ()


def test_node_def_script_type() -> None:
    node = NodeDef(id="s1", type="script", run="myscripts:prep", output_ports=(Port("out"),))
    assert node.type == "script"
    assert node.run == "myscripts:prep"
    assert node.tools == ()
    updated = dataclasses.replace(node, run="xdog.flow.tools:other")
    assert updated.run == "xdog.flow.tools:other"
    assert node.run == "myscripts:prep"


def test_node_def_agent_with_tools() -> None:
    node = NodeDef(id="a1", tools=("echo", "search"))
    assert node.type == "agent"
    assert node.tools == ("echo", "search")
    assert node.run is None
    updated = dataclasses.replace(node, tools=("echo",))
    assert updated.tools == ("echo",)
    assert node.tools == ("echo", "search")


def test_node_def_inputs_default() -> None:
    node = NodeDef(id="n1")
    assert node.input_ports == ()
    assert node.input_names == ()


def test_node_def_inputs_construction() -> None:
    node = NodeDef(id="n1", input_ports=(Port("a"), Port("b")))
    assert node.input_ports == (Port("a"), Port("b"))
    assert node.input_names == ("a", "b")


def test_agent_output_schema_derived_from_multi_ports() -> None:
    from xdog.flow.models import Port, agent_is_structured, agent_output_schema

    node = NodeDef(
        id="n1",
        type="agent",
        output_ports=(Port("summary", schema={"type": "string"}), Port("count", schema={"type": "integer"})),
    )
    assert agent_is_structured(node) is True
    assert agent_output_schema(node) == {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["summary", "count"],
    }


def test_agent_single_string_port_is_plain_text() -> None:
    from xdog.flow.models import Port, agent_is_structured

    node = NodeDef(id="n1", type="agent", output_ports=(Port("answer"),))
    assert agent_is_structured(node) is False


def test_agent_single_structured_port_uses_its_schema() -> None:
    from xdog.flow.models import Port, agent_is_structured, agent_output_schema

    plan = {"type": "object", "properties": {"x": {"type": "integer"}}}
    node = NodeDef(id="n1", type="agent", output_ports=(Port("plan", schema=plan),))
    assert agent_is_structured(node) is True
    assert agent_output_schema(node) == plan


def test_the_model_types_are_actually_frozen() -> None:
    """What the five deleted "immutability" tests never checked.

    They each called `dataclasses.replace` and asserted the copy differed from
    the original — which is the documented behaviour of `dataclasses.replace`,
    not of anything here. Removing `frozen=True` from every model would have
    left all five green. This one fails.
    """
    subjects = (
        (NodeDef(id="n1"), "id", "n2"),
        (Condition(op="equals", value="a"), "op", "contains"),
        (EdgeDef(src="a", dst="b"), "dst", "c"),
        (WorkflowDef(name="w", provider="p", entry="n1", nodes=(), edges=()), "name", "other"),
        (Port(name="p"), "name", "q"),
    )
    for obj, field, value in subjects:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, value)
