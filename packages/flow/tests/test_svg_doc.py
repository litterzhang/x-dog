"""Acceptance contract for the (flow-generated) SVG-document embed/extract.

``flow.builder.svg_doc`` makes an SVG a dual-purpose file: a rendered diagram
that also carries the full workflow JSON, so the builder can reload and edit it.

The central law (the source of truth is the embedded JSON, the drawing is
derived)::

    parse_workflow(read_workflow_from_svg(workflow_to_svg_document(wf))) == wf
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

import pytest
from flow.builder.svg_doc import (
    dump_workflow_svg,
    read_workflow_from_svg,
    workflow_to_svg_document,
)
from flow.errors import WorkflowValidationError
from flow.loader import load_workflow, parse_workflow
from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef

_EXAMPLES = sorted((pathlib.Path(__file__).parent.parent / "examples").glob("*.json"))
_SVG_NS = "http://www.w3.org/2000/svg"


def _rich() -> WorkflowDef:
    return WorkflowDef(
        name="rich",
        provider="copilot",
        entry="a",
        default_model="claude-sonnet-4.5",
        initial_state=(("topic", "x"),),
        nodes=(
            NodeDef(id="a", type="script", run="flow.tools:passthrough", output="rec"),
            NodeDef(
                id="b",
                type="agent",
                inputs=("rec",),
                tools=("echo",),
                system_prompt="sys\nmultiline",
                prompt="do {{rec}}",
                output="out",
                output_schema=(("k1", "string"), ("k2", "integer")),
            ),
        ),
        edges=(
            EdgeDef(src="a", dst="b"),
            EdgeDef(
                src="b",
                dst="a",
                when=Condition(op="contains", value="{{out}}", text="REVISE"),
                loop_max=2,
            ),
        ),
    )


@pytest.mark.parametrize("example", _EXAMPLES, ids=lambda p: p.name)
def test_roundtrip_examples_through_svg(example: pathlib.Path) -> None:
    wf = load_workflow(example)
    doc = workflow_to_svg_document(wf)
    assert parse_workflow(read_workflow_from_svg(doc)) == wf


def test_roundtrip_rich_case() -> None:
    wf = _rich()
    doc = workflow_to_svg_document(wf)
    assert parse_workflow(read_workflow_from_svg(doc)) == wf


def test_document_is_valid_svg() -> None:
    doc = workflow_to_svg_document(_rich())
    root = ET.fromstring(doc)  # still a well-formed SVG, so it renders
    assert root.tag in ("svg", f"{{{_SVG_NS}}}svg")


def test_document_embeds_json_marker() -> None:
    doc = workflow_to_svg_document(_rich())
    # a discoverable marker element carries the JSON
    assert "flow-workflow" in doc


def test_read_from_path(tmp_path: pathlib.Path) -> None:
    wf = _rich()
    p = tmp_path / "wf.svg"
    dump_workflow_svg(wf, p)
    assert p.exists()
    assert parse_workflow(read_workflow_from_svg(p)) == wf


def test_read_svg_without_marker_raises(tmp_path: pathlib.Path) -> None:
    plain = tmp_path / "plain.svg"
    plain.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', encoding="utf-8")
    with pytest.raises(WorkflowValidationError):
        read_workflow_from_svg(plain)


def test_multiline_prompt_survives_embedding() -> None:
    wf = _rich()  # node 'b' has a multiline system_prompt
    reloaded = parse_workflow(read_workflow_from_svg(workflow_to_svg_document(wf)))
    assert reloaded.nodes[1].system_prompt == "sys\nmultiline"
