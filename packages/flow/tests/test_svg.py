"""Acceptance contract for the (flow-generated) SVG renderer ``flow.graph.to_svg``.

This is the SPEC the code generator must satisfy. ``to_svg(wf) -> str`` renders a
workflow to a standalone, valid SVG document (nodes as boxes, edges as arrows,
conditional/loop edges labelled). Pure function, deterministic, no I/O.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from flow.graph import to_svg
from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef

_SVG_NS = "http://www.w3.org/2000/svg"


def _wf() -> WorkflowDef:
    return WorkflowDef(
        name="demo",
        provider="copilot",
        entry="a",
        default_model="m",
        nodes=(
            NodeDef(id="a", type="agent", prompt="p", output="x"),
            NodeDef(id="b", type="script", run="flow.tools:passthrough", output="y"),
            NodeDef(id="c", type="agent", prompt="q", output="z"),
        ),
        edges=(
            EdgeDef(src="a", dst="b"),
            EdgeDef(src="b", dst="c"),
            EdgeDef(
                src="c",
                dst="b",
                when=Condition(op="contains", value="{{z}}", text="REVISE"),
                loop_max=2,
            ),
        ),
    )


def test_to_svg_is_valid_xml_with_svg_root() -> None:
    svg = to_svg(_wf())
    root = ET.fromstring(svg)  # raises if not well-formed XML
    # root tag is <svg> (namespaced or not)
    assert root.tag in ("svg", f"{{{_SVG_NS}}}svg")


def test_to_svg_contains_every_node_id() -> None:
    svg = to_svg(_wf())
    for node_id in ("a", "b", "c"):
        assert node_id in svg


def test_to_svg_has_a_rect_per_node() -> None:
    svg = to_svg(_wf())
    root = ET.fromstring(svg)
    rects = [el for el in root.iter() if el.tag in ("rect", f"{{{_SVG_NS}}}rect")]
    assert len(rects) >= 3  # at least one box per node


def test_to_svg_labels_conditional_edge() -> None:
    svg = to_svg(_wf())
    # the conditional back-edge carries a REVISE-ish label somewhere in the text
    assert "REVISE" in svg or "contains" in svg


def test_to_svg_is_deterministic() -> None:
    wf = _wf()
    assert to_svg(wf) == to_svg(wf)


def test_to_svg_empty_workflow_is_valid() -> None:
    wf = WorkflowDef(name="empty", provider="copilot", entry="", nodes=(), edges=())
    svg = to_svg(wf)
    root = ET.fromstring(svg)
    assert root.tag in ("svg", f"{{{_SVG_NS}}}svg")
