"""Round-trip tests for flow.builder.serialize.

The law: ``parse_workflow(workflow_to_dict(wf)) == wf`` for every workflow the
loader accepts.  We prove it against every shipped example plus a hand-built
workflow that exercises conditions, loops, inputs, and structured output ports.
"""

from __future__ import annotations

import json
import pathlib

from xdog.flow.builder.serialize import dump_workflow, workflow_to_dict
from xdog.flow.loader import load_workflow, parse_workflow
from xdog.flow.models import Condition, EdgeDef, NodeDef, Port, WorkflowDef

# Workflow examples only: *.test.json siblings are test suites, not workflows.
# Recursive: an example that ships sibling ``run:`` modules lives in its own
# directory, and it should be held to the same round-trip contract as a flat one.
_EXAMPLES = sorted(
    p for p in (pathlib.Path(__file__).parent.parent / "examples").rglob("*.json")
    if not p.name.endswith(".test.json")
)


def test_roundtrip_examples() -> None:
    """Every shipped example survives dict round-tripping.

    One test over the corpus rather than one per file: a broken example is
    still named, because failures are collected and reported together.
    """
    broken = [
        example.name
        for example in _EXAMPLES
        if parse_workflow(workflow_to_dict(load_workflow(example))) != load_workflow(example)
    ]
    assert not broken, f"examples that do not round-trip: {broken}"


def _rich_workflow() -> WorkflowDef:
    return WorkflowDef(
        name="rich",
        provider="copilot",
        entry="a",
        default_model="claude-sonnet-4.5",
        initial_state=(("topic", "x"),),
        nodes=(
            NodeDef(id="a", type="script", run="myscripts:prep", output_ports=(Port("rec"),)),
            NodeDef(
                id="b",
                type="agent",
                input_ports=(Port("rec"),),
                tools=("echo",),
                system_prompt="sys",
                prompt="do {{rec}}",
                # Structured output: the whole submitted object lands in "out".
                output_ports=(
                    Port(
                        "out",
                        schema={
                            "type": "object",
                            "properties": {"k1": {"type": "string"}, "k2": {"type": "integer"}},
                        },
                    ),
                ),
            ),
            NodeDef(
                id="c",
                type="agent",
                prompt="review {{out}}",
                input_ports=(Port("out", required=False),),
                output_ports=(Port("verdict"),),
            ),
        ),
        edges=(
            EdgeDef(src="a", dst="b", mapping=(("rec", "rec"),)),
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


def test_roundtrip_non_required_input_port() -> None:
    """A non-required input survives canonical schema serialization."""
    wf = WorkflowDef(
        name="opt",
        provider="copilot",
        entry="a",
        default_model="m",
        initial_state=(("topic", "x"),),
        nodes=(
            NodeDef(
                id="a",
                type="agent",
                # a plain string optional port must still emit the object form
                input_ports=(Port("topic"), Port("feedback", required=False)),
                prompt="{{topic}} {{feedback}}",
                output_ports=(Port("answer"),),
            ),
            NodeDef(
                id="b",
                type="agent",
                input_ports=(Port("answer"),),
                prompt="{{answer}}",
                output_ports=(Port("feedback"),),
            ),
        ),
        edges=(
            EdgeDef(src="$in", dst="a", mapping=(("topic", "topic"),)),
            EdgeDef(src="a", dst="b", mapping=(("answer", "answer"),)),
            EdgeDef(
                src="b",
                dst="a",
                mapping=(("feedback", "feedback"),),
                when=Condition(op="contains", value="{{feedback}}", text="REVISE"),
                loop_max=2,
            ),
        ),
    )
    dumped = workflow_to_dict(wf)
    a_inputs = next(n for n in dumped["nodes"] if n["id"] == "a")["inputs"]
    assert {"name": "feedback", "schema": {"type": "string"}, "required": False} in a_inputs
    assert parse_workflow(dumped) == wf


def test_roundtrip_nested_schema_port() -> None:
    """A port carrying a nested JSON Schema survives serialize -> parse."""
    plan_schema: dict[str, object] = {
        "type": "object",
        "properties": {"owner": {"type": "string"}, "tasks": {"type": "array", "items": {"type": "string"}}},
        "required": ["owner", "tasks"],
    }
    wf = WorkflowDef(
        name="nested",
        provider="copilot",
        entry="a",
        default_model="m",
        initial_state=(("topic", "x"),),
        nodes=(
            NodeDef(
                id="a",
                type="script",
                input_ports=(Port("topic", schema={"type": "string"}),),
                code="def a(ctx, topic):\n    return {'owner': topic, 'tasks': []}",
                output_ports=(Port("plan", schema=plan_schema),),
            ),
        ),
        edges=(EdgeDef(src="$in", dst="a", mapping=(("topic", "topic"),)),),
    )
    dumped = workflow_to_dict(wf)
    a_outputs = next(n for n in dumped["nodes"] if n["id"] == "a")["outputs"]
    assert a_outputs == [{"name": "plan", "schema": plan_schema, "required": True}]
    assert parse_workflow(dumped) == wf


def test_roundtrip_tool_manifest() -> None:
    wf = WorkflowDef(
        name="tw",
        provider="copilot",
        entry="a",
        nodes=(
            NodeDef(
                id="a",
                prompt="p",
                input_ports=(Port("topic"),),
                output_ports=(Port("x"),),
                tools=("reverse",),
            ),
        ),
        edges=(EdgeDef(src="$in", dst="a", mapping=(("topic", "topic"),)),),
        initial_state=(("topic", "hi"),),
        tool_refs=(("reverse", "mytools:make_reverse"), ("weather", "mytools:MY_WEATHER_TOOL")),
    )
    assert parse_workflow(workflow_to_dict(wf)) == wf


def test_roundtrip_nested_condition() -> None:
    wf = WorkflowDef(
        name="cond",
        provider="copilot",
        entry="a",
        nodes=(
            NodeDef(id="a", prompt="p", output_ports=(Port("x"),)),
            NodeDef(id="b", prompt="q", output_ports=(Port("y"),)),
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
