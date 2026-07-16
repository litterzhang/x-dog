"""Round-trip tests for flow.builder.serialize.

The law: ``parse_workflow(workflow_to_dict(wf)) == wf`` for every workflow the
loader accepts.  We prove it against every shipped example plus a hand-built
workflow that exercises conditions, loops, inputs, and output_schema.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from flow.builder.serialize import dump_workflow, workflow_to_dict
from flow.loader import load_workflow, parse_workflow
from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef

_EXAMPLES = sorted((pathlib.Path(__file__).parent.parent / "examples").glob("*.json"))


@pytest.mark.parametrize("example", _EXAMPLES, ids=lambda p: p.name)
def test_roundtrip_examples(example: pathlib.Path) -> None:
    wf = load_workflow(example)
    assert parse_workflow(workflow_to_dict(wf)) == wf


def _rich_workflow() -> WorkflowDef:
    return WorkflowDef(
        name="rich",
        provider="copilot",
        entry="a",
        default_model="claude-sonnet-4.5",
        initial_state=(("topic", "x"),),
        nodes=(
            NodeDef(id="a", type="script", run="myscripts:prep", output="rec"),
            NodeDef(
                id="b",
                type="agent",
                inputs=("rec",),
                tools=("echo",),
                system_prompt="sys",
                prompt="do {{rec}}",
                output="out",
                output_schema=(("k1", "string"), ("k2", "integer")),
            ),
            NodeDef(id="c", type="agent", prompt="review {{out}}", output="verdict"),
        ),
        edges=(
            EdgeDef(src="a", dst="b"),
            EdgeDef(src="b", dst="c"),
            EdgeDef(
                src="c",
                dst="b",
                when=Condition(op="contains", value="{{verdict}}", text="REVISE"),
                loop_max=2,
            ),
        ),
    )


def test_roundtrip_rich() -> None:
    wf = _rich_workflow()
    assert parse_workflow(workflow_to_dict(wf)) == wf


def test_roundtrip_nested_condition() -> None:
    wf = WorkflowDef(
        name="cond",
        provider="copilot",
        entry="a",
        nodes=(
            NodeDef(id="a", prompt="p", output="x"),
            NodeDef(id="b", prompt="q", output="y"),
        ),
        edges=(
            EdgeDef(
                src="a",
                dst="b",
                when=Condition(
                    op="and",
                    children=(
                        Condition(op="contains", value="{{x}}", text="ok"),
                        Condition(op="not", children=(Condition(op="equals", value="{{x}}", text="no"),)),
                    ),
                ),
            ),
        ),
    )
    assert parse_workflow(workflow_to_dict(wf)) == wf


def test_dump_workflow_writes_runnable_json(tmp_path: pathlib.Path) -> None:
    wf = _rich_workflow()
    out = tmp_path / "wf.json"
    dump_workflow(wf, out)
    # file is valid JSON and reloads to an equal workflow
    reloaded = load_workflow(out)
    assert reloaded == wf
    # pretty-printed (indented)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("{\n")
    json.loads(text)  # no exception
