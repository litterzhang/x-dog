"""Tests for flow.graph — to_ascii and to_mermaid."""

from __future__ import annotations

from flow.graph import to_ascii, to_mermaid
from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef


def _simple_wf() -> WorkflowDef:
    return WorkflowDef(
        name="test",
        provider="mock",
        entry="a",
        nodes=(NodeDef(id="a"), NodeDef(id="b"), NodeDef(id="c")),
        edges=(
            EdgeDef(src="a", dst="b"),
            EdgeDef(src="b", dst="c"),
        ),
    )


def _conditional_wf() -> WorkflowDef:
    cond = Condition(op="equals", value="REVISE")
    return WorkflowDef(
        name="cond",
        provider="mock",
        entry="start",
        nodes=(NodeDef(id="start"), NodeDef(id="end")),
        edges=(EdgeDef(src="start", dst="end", when=cond),),
    )


def _loop_wf() -> WorkflowDef:
    return WorkflowDef(
        name="loop",
        provider="mock",
        entry="x",
        nodes=(NodeDef(id="x"), NodeDef(id="y")),
        edges=(EdgeDef(src="x", dst="y", loop_max=3),),
    )


# --- to_mermaid ---


def test_mermaid_starts_with_flowchart_td() -> None:
    result = to_mermaid(_simple_wf())
    assert result.startswith("flowchart TD")


def test_mermaid_includes_all_node_ids() -> None:
    result = to_mermaid(_simple_wf())
    for node_id in ("a", "b", "c"):
        assert node_id in result


def test_mermaid_includes_all_edges() -> None:
    result = to_mermaid(_simple_wf())
    assert "a" in result and "b" in result and "c" in result
    assert "-->" in result


def test_mermaid_conditional_edge_label() -> None:
    result = to_mermaid(_conditional_wf())
    assert "start" in result
    assert "end" in result
    assert "equals:REVISE" in result


def test_mermaid_loop_edge_label() -> None:
    result = to_mermaid(_loop_wf())
    assert "loop" in result
    assert "x" in result
    assert "y" in result


# --- to_ascii ---


def test_ascii_includes_all_node_ids() -> None:
    result = to_ascii(_simple_wf())
    for node_id in ("a", "b", "c"):
        assert node_id in result


def test_ascii_includes_all_edges() -> None:
    result = to_ascii(_simple_wf())
    assert "a -> b" in result
    assert "b -> c" in result


def test_ascii_conditional_annotation() -> None:
    result = to_ascii(_conditional_wf())
    assert "when:" in result
    assert "equals:REVISE" in result


def test_ascii_loop_annotation() -> None:
    result = to_ascii(_loop_wf())
    assert "loop_max: 3" in result


def test_ascii_workflow_name_and_entry() -> None:
    result = to_ascii(_simple_wf())
    assert "workflow: test" in result
    assert "entry: a" in result
