"""Tests for flow.models — construction and immutability checks."""

from __future__ import annotations

import dataclasses

from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef


def test_node_def_defaults() -> None:
    node = NodeDef(id="n1")
    assert node.id == "n1"
    assert node.type == "agent"
    assert node.model is None
    assert node.system_prompt == ""
    assert node.prompt == ""
    assert node.output is None


def test_node_def_replace_immutability() -> None:
    node = NodeDef(id="n1", prompt="hello")
    updated = dataclasses.replace(node, prompt="world")
    assert updated.prompt == "world"
    assert node.prompt == "hello"
    assert updated is not node


def test_condition_defaults() -> None:
    cond = Condition(op="equals")
    assert cond.op == "equals"
    assert cond.value is None
    assert cond.text is None
    assert cond.children == ()


def test_condition_with_children() -> None:
    child = Condition(op="contains", text="ok")
    parent = Condition(op="and", children=(child,))
    assert len(parent.children) == 1
    assert parent.children[0].text == "ok"


def test_condition_replace_immutability() -> None:
    cond = Condition(op="equals", value="yes")
    updated = dataclasses.replace(cond, value="no")
    assert updated.value == "no"
    assert cond.value == "yes"
    assert updated is not cond


def test_edge_def_defaults() -> None:
    edge = EdgeDef(src="a", dst="b")
    assert edge.src == "a"
    assert edge.dst == "b"
    assert edge.when is None
    assert edge.loop_max is None


def test_edge_def_replace_immutability() -> None:
    edge = EdgeDef(src="a", dst="b", loop_max=3)
    updated = dataclasses.replace(edge, loop_max=5)
    assert updated.loop_max == 5
    assert edge.loop_max == 3
    assert updated is not edge


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


def test_workflow_def_replace_immutability() -> None:
    node = NodeDef(id="n1")
    wf = WorkflowDef(
        name="flow-a",
        provider="anthropic",
        entry="n1",
        nodes=(node,),
        edges=(),
    )
    updated = dataclasses.replace(wf, name="flow-b")
    assert updated.name == "flow-b"
    assert wf.name == "flow-a"
    assert updated is not wf


def test_workflow_def_initial_state() -> None:
    node = NodeDef(id="n1")
    wf = WorkflowDef(
        name="flow",
        provider="anthropic",
        entry="n1",
        nodes=(node,),
        edges=(),
        initial_state=(("key", "value"),),
    )
    assert wf.initial_state == (("key", "value"),)
