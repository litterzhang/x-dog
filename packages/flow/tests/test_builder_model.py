"""Headless tests for the builder model + actions (no terminal)."""

from __future__ import annotations

import json
from dataclasses import replace

from xdog.flow.builder import actions
from xdog.flow.builder.model import BuilderModel, empty_model, model_from_workflow
from xdog.flow.builder.serialize import workflow_to_dict
from xdog.flow.loader import parse_workflow
from xdog.flow.models import Condition, EdgeDef, Port


def _add_mapped_edge(
    model: BuilderModel,
    src: str,
    dst: str,
    mapping: tuple[tuple[str, str], ...],
) -> BuilderModel:
    """Append a data edge carrying an explicit port mapping.

    ``actions.add_edge`` only builds control edges (no mapping), so tests that
    need a downstream input port to be *fed* wire the mapped edge directly.
    """
    edge = EdgeDef(src=src, dst=dst, mapping=mapping)
    return model.with_wf(replace(model.wf, edges=model.wf.edges + (edge,)))


def test_empty_model_is_invalid_until_entry_exists() -> None:
    m = empty_model()
    assert m.wf.nodes == ()
    # empty entry '' is not a real node -> validation error
    assert m.error is not None


def test_add_node_sets_entry_and_selection() -> None:
    m = empty_model()
    m = actions.add_node(m, "agent")
    assert m.node_ids == ("agent",)
    assert m.wf.entry == "agent"  # first node becomes entry
    assert m.selected == "agent"
    assert m.dirty is True
    assert m.error is None  # a single entry node with no edges validates


def test_add_node_generates_unique_ids() -> None:
    m = empty_model()
    m = actions.add_node(m, "agent")
    m = actions.add_node(m, "agent")
    m = actions.add_node(m, "agent")
    assert m.node_ids == ("agent", "agent2", "agent3")


def test_set_field_and_clear_optional() -> None:
    m = actions.add_node(empty_model(), "agent")
    m = actions.set_field(m, "agent", "prompt", "hello {{topic}}")
    assert m.wf.nodes[0].prompt == "hello {{topic}}"
    m = actions.set_field(m, "agent", "model", "claude-sonnet-4.5")
    assert m.wf.nodes[0].model == "claude-sonnet-4.5"
    m = actions.set_field(m, "agent", "model", "")  # clears to None
    assert m.wf.nodes[0].model is None


def test_rename_node_updates_edges_and_entry() -> None:
    m = empty_model()
    m = actions.add_node(m, "agent")  # 'agent' (entry)
    m = actions.add_node(m, "agent")  # 'agent2'
    m = actions.add_edge(m, "agent", "agent2")
    m = actions.select(m, "agent")  # select the node we're about to rename
    m = actions.rename_node(m, "agent", "start")
    assert m.wf.entry == "start"
    assert m.wf.edges[0].src == "start"
    assert m.selected == "start"  # selection follows the rename
    assert "start" in m.node_ids


def test_remove_node_drops_touching_edges() -> None:
    m = empty_model()
    m = actions.add_node(m, "agent")
    m = actions.add_node(m, "agent")
    m = actions.add_edge(m, "agent", "agent2")
    m = actions.remove_node(m, "agent2")
    assert m.node_ids == ("agent",)
    assert m.wf.edges == ()


def test_unreachable_input_surfaces_validation_error() -> None:
    m = empty_model()
    m = actions.add_node(m, "agent")  # entry 'agent'
    m = actions.add_node(m, "agent")  # 'agent2'
    m = actions.add_edge(m, "agent", "agent2")
    # agent2 declares an input port nobody feeds -> live validation error
    m = actions.set_input_ports(m, "agent2", (Port("missing_key"),))
    assert m.error is not None
    assert "missing_key" in m.error


def test_reachable_input_is_valid() -> None:
    m = empty_model()
    m = actions.add_node(m, "agent")
    m = actions.set_output_ports(m, "agent", (Port("rec"),))
    m = actions.add_node(m, "agent")
    m = actions.set_input_ports(m, "agent2", (Port("rec"),))
    # the mapped edge feeds agent2's 'rec' input from agent's 'rec' output
    m = _add_mapped_edge(m, "agent", "agent2", (("rec", "rec"),))
    assert m.error is None


def test_backedge_needs_loop_max() -> None:
    m = empty_model()
    m = actions.add_node(m, "agent")  # a (entry)
    m = actions.add_node(m, "agent")  # agent2
    m = actions.add_edge(m, "agent", "agent2")
    # back-edge without loop_max is invalid
    m = actions.add_edge(m, "agent2", "agent")
    assert m.error is not None
    # adding loop_max fixes it
    m2 = actions.remove_edge(m, 1)
    m2 = actions.add_edge(
        m2,
        "agent2",
        "agent",
        when=Condition(op="contains", value="literal", text="X"),
        loop_max=2,
    )
    assert m2.error is None


def test_set_tools() -> None:
    m = actions.add_node(empty_model(), "agent")
    m = actions.set_tools(m, "agent", ("echo", "filesystem"))
    node = m.wf.nodes[0]
    assert node.tools == ("echo", "filesystem")


def test_script_node_type_and_run() -> None:
    m = actions.add_node(empty_model(), "script")
    assert m.wf.nodes[0].type == "script"
    m = actions.set_field(m, "script", "run", "myscripts:prep")
    assert m.wf.nodes[0].run == "myscripts:prep"
    assert m.error is None


def test_build_then_serialize_roundtrips() -> None:
    """A workflow built entirely through actions serializes to runnable JSON."""
    m = empty_model("built", "copilot")
    m = actions.set_default_model(m, "claude-sonnet-4.5")
    m = actions.set_initial_state(m, (("topic", "x"),))
    m = actions.add_node(m, "script")
    m = actions.set_field(m, "script", "run", "myscripts:prep")
    m = actions.set_output_ports(m, "script", (Port("rec"),))
    m = actions.add_node(m, "agent")
    m = actions.set_field(m, "agent", "prompt", "do {{rec}}")
    m = actions.set_output_ports(m, "agent", (Port("out"),))
    m = actions.set_input_ports(m, "agent", (Port("rec"),))
    m = _add_mapped_edge(m, "script", "agent", (("rec", "rec"),))
    assert m.error is None
    # round-trips through JSON
    reparsed = parse_workflow(workflow_to_dict(m.wf))
    assert reparsed == m.wf
    # and it's real JSON
    json.dumps(workflow_to_dict(m.wf))


def test_model_from_workflow_selects_first() -> None:
    m = actions.add_node(empty_model(), "agent")
    m2 = model_from_workflow(m.wf)
    assert isinstance(m2, BuilderModel)
    assert m2.selected == m.wf.nodes[0].id
    assert m2.dirty is False
