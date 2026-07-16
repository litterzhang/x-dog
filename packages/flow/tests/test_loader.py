"""Tests for flow.loader — load_workflow, parse_workflow, validate_workflow."""

from __future__ import annotations

from pathlib import Path

import pytest
from flow.errors import WorkflowValidationError
from flow.loader import load_workflow, parse_workflow, validate_workflow
from flow.models import WorkflowDef

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_linear_ok() -> None:
    wf = load_workflow(FIXTURES / "linear.json")
    assert isinstance(wf, WorkflowDef)
    assert wf.name == "linear-workflow"
    assert wf.entry == "a"
    assert wf.provider == "anthropic"
    assert wf.default_model == "claude-haiku-4-5"
    assert len(wf.nodes) == 3
    assert [n.id for n in wf.nodes] == ["a", "b", "c"]
    assert len(wf.edges) == 2
    assert wf.edges[0].src == "a" and wf.edges[0].dst == "b"
    assert wf.edges[1].src == "b" and wf.edges[1].dst == "c"
    assert wf.initial_state == (("result", ""),)


def test_load_bad_missing_entry_raises() -> None:
    with pytest.raises(WorkflowValidationError, match="Entry node"):
        load_workflow(FIXTURES / "bad_missing_entry.json")


def test_cyclic_edge_without_loop_max_raises() -> None:
    data = {
        "name": "cyclic",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a"},  # back-edge, no loop.max
        ],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="loop.max"):
        validate_workflow(wf)


def test_cyclic_edge_with_loop_max_ok() -> None:
    data = {
        "name": "loop-ok",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a", "loop": {"max": 3}},
        ],
    }
    wf = parse_workflow(data)
    validate_workflow(wf)  # should not raise
    assert wf.edges[1].loop_max == 3


def test_duplicate_node_ids_raises() -> None:
    data = {
        "name": "dup",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}, {"id": "a"}],
        "edges": [],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="Duplicate"):
        validate_workflow(wf)


def test_edge_unknown_src_raises() -> None:
    data = {
        "name": "bad-edge",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}],
        "edges": [{"from": "x", "to": "a"}],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="src"):
        validate_workflow(wf)


def test_edge_unknown_dst_raises() -> None:
    data = {
        "name": "bad-dst",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}],
        "edges": [{"from": "a", "to": "z"}],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="dst"):
        validate_workflow(wf)


def test_parse_condition_equals() -> None:
    data = {
        "name": "cond",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [{"from": "a", "to": "b", "when": {"equals": {"value": "yes", "text": "output"}}}],
    }
    wf = parse_workflow(data)
    cond = wf.edges[0].when
    assert cond is not None
    assert cond.op == "equals"
    assert cond.value == "yes"
    assert cond.text == "output"


def test_parse_condition_and_or() -> None:
    data = {
        "name": "cond2",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {
                "from": "a",
                "to": "b",
                "when": {
                    "and": [
                        {"equals": {"value": "x", "text": "t"}},
                        {"not": {"contains": {"value": "y", "text": "t"}}},
                    ]
                },
            }
        ],
    }
    wf = parse_workflow(data)
    cond = wf.edges[0].when
    assert cond is not None
    assert cond.op == "and"
    assert len(cond.children) == 2
    assert cond.children[0].op == "equals"
    assert cond.children[1].op == "not"
    assert cond.children[1].children[0].op == "contains"


def test_load_tools_script_ok() -> None:
    wf = load_workflow(FIXTURES / "tools_script.json")
    assert wf.provider == "copilot"
    assert wf.default_model == "claude-sonnet-4.5"
    assert wf.entry == "prep"
    node_map = {n.id: n for n in wf.nodes}
    prep = node_map["prep"]
    analyze = node_map["analyze"]
    assert prep.type == "script"
    assert prep.run == "flow.tools:passthrough"
    assert analyze.tools == ("echo",)
    assert analyze.type == "agent"


def test_script_node_missing_run_raises() -> None:
    data = {
        "name": "bad-script",
        "provider": "copilot",
        "entry": "s",
        "nodes": [{"id": "s", "type": "script"}],
        "edges": [],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="run"):
        validate_workflow(wf)


def test_agent_node_with_run_raises() -> None:
    data = {
        "name": "bad-agent",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "run": "flow.tools:passthrough"}],
        "edges": [],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="must not set"):
        validate_workflow(wf)


def test_script_node_bad_run_format_raises() -> None:
    data = {
        "name": "bad-run",
        "provider": "copilot",
        "entry": "s",
        "nodes": [{"id": "s", "type": "script", "run": "not-valid-format"}],
        "edges": [],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="module.path:callable"):
        validate_workflow(wf)


def test_load_linear_inputs_parsed() -> None:
    wf = load_workflow(FIXTURES / "linear.json")
    node_map = {n.id: n for n in wf.nodes}
    assert node_map["a"].inputs == ()
    assert node_map["b"].inputs == ("step_a_out",)
    assert node_map["c"].inputs == ("step_b_out",)


def test_load_tools_script_inputs_parsed() -> None:
    wf = load_workflow(FIXTURES / "tools_script.json")
    node_map = {n.id: n for n in wf.nodes}
    assert node_map["prep"].inputs == ()
    assert node_map["analyze"].inputs == ("prepped",)


def test_bad_unreachable_input_raises() -> None:
    with pytest.raises(WorkflowValidationError, match="missing_key"):
        load_workflow(FIXTURES / "bad_unreachable_input.json")


def test_input_in_initial_state_validates_ok() -> None:
    data = {
        "name": "ok-initial",
        "provider": "anthropic",
        "entry": "a",
        "state": {"seed": "hello"},
        "nodes": [{"id": "a", "inputs": ["seed"]}],
        "edges": [],
    }
    wf = parse_workflow(data)
    validate_workflow(wf)  # should not raise


def test_unreachable_input_inline_raises() -> None:
    data = {
        "name": "bad-inline",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a", "inputs": ["ghost"]}],
        "edges": [],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="ghost"):
        validate_workflow(wf)


def test_output_schema_parsed() -> None:
    data = {
        "name": "schema-test",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a", "output_schema": {"name": "string", "count": "integer"}}],
        "edges": [],
    }
    wf = parse_workflow(data)
    node = wf.nodes[0]
    assert set(node.output_schema) == {("name", "string"), ("count", "integer")}


def test_output_schema_default_empty() -> None:
    data = {
        "name": "no-schema",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}],
        "edges": [],
    }
    wf = parse_workflow(data)
    assert wf.nodes[0].output_schema == ()


# --- typed inline script nodes (code XOR run, signature validation) ----------


def _wf_with_script(node: dict) -> dict:
    return {
        "name": "t",
        "provider": "copilot",
        "entry": node["id"],
        "nodes": [node],
        "edges": [],
    }


def test_inline_script_parses_typed_io() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow({
        "name": "t",
        "provider": "copilot",
        "entry": "add",
        "state": {"a": "3", "b": "4"},  # inputs reachable from initial state
        "nodes": [{
            "id": "add",
            "type": "script",
            "code": "def add(ctx, a, b):\n    return a + b",
            "inputs": [{"name": "a", "type": "integer"}, {"name": "b", "type": "integer"}],
            "output": {"name": "sum", "type": "integer"},
        }],
        "edges": [],
    })
    validate_workflow(wf)  # must not raise
    n = wf.nodes[0]
    assert n.inputs == ("a", "b")
    assert n.input_schema == (("a", "integer"), ("b", "integer"))
    assert n.output == "sum"
    assert n.output_type == "integer"
    assert n.code is not None


def test_inline_bad_syntax_raises() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(_wf_with_script({
        "id": "x", "type": "script", "code": "def x(ctx):\n    return (", "output": "y",
    }))
    with pytest.raises(WorkflowValidationError, match="invalid code"):
        validate_workflow(wf)


def test_inline_first_param_must_be_ctx() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(_wf_with_script({
        "id": "x", "type": "script", "code": "def x(a):\n    return a",
        "inputs": [{"name": "a", "type": "string"}], "output": "y",
    }))
    with pytest.raises(WorkflowValidationError, match="first parameter must be 'ctx'"):
        validate_workflow(wf)


def test_inline_params_must_match_inputs() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(_wf_with_script({
        "id": "x", "type": "script", "code": "def x(ctx, a, b):\n    return a",
        "inputs": [{"name": "a", "type": "string"}], "output": "y",
    }))
    with pytest.raises(WorkflowValidationError, match="!= declared inputs"):
        validate_workflow(wf)


def test_script_code_and_run_both_set_raises() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(_wf_with_script({
        "id": "x", "type": "script", "code": "def x(ctx):\n    return ''",
        "run": "m:f", "output": "y",
    }))
    with pytest.raises(WorkflowValidationError, match="not both"):
        validate_workflow(wf)


def test_script_neither_code_nor_run_raises() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(_wf_with_script({"id": "x", "type": "script", "output": "y"}))
    with pytest.raises(WorkflowValidationError, match="must set 'code' or 'run'"):
        validate_workflow(wf)
