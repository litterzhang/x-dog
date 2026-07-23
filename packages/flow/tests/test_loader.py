"""Tests for flow.loader — load_workflow, parse_workflow, validate_workflow."""

from __future__ import annotations

from pathlib import Path

import pytest
from flow.errors import WorkflowValidationError
from flow.loader import load_workflow, parse_workflow, validate_workflow
from flow.models import Port, WorkflowDef

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


def test_output_sink_edge_ok() -> None:
    """An edge to the reserved $output sink validates when the source port exists."""
    data = {
        "name": "out-ok",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a", "output": "r"}],
        "edges": [{"from": "a", "to": "$output", "map": {"r": "result"}}],
    }
    validate_workflow(parse_workflow(data))


def test_output_sink_unknown_source_port_raises() -> None:
    """An $output edge whose source port does not exist fails validation."""
    data = {
        "name": "out-bad",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a", "output": "r"}],
        "edges": [{"from": "a", "to": "$output", "map": {"nope": "result"}}],
    }
    with pytest.raises(WorkflowValidationError, match="output port"):
        validate_workflow(parse_workflow(data))


def test_output_as_edge_source_raises() -> None:
    """$output is a sink only; using it as an edge source is rejected."""
    data = {
        "name": "out-src",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a", "inputs": ["x"]}],
        "edges": [{"from": "$output", "to": "a", "map": {"x": "x"}}],
    }
    with pytest.raises(WorkflowValidationError, match="sink only"):
        validate_workflow(parse_workflow(data))


def test_output_reserved_node_id_raises() -> None:
    """A real node may not claim the reserved $output id."""
    data = {
        "name": "out-id",
        "provider": "anthropic",
        "entry": "$output",
        "nodes": [{"id": "$output"}],
        "edges": [],
    }
    with pytest.raises(WorkflowValidationError, match="reserved"):
        validate_workflow(parse_workflow(data))


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
    assert prep.code is not None and prep.code.startswith("def prep(ctx")
    assert prep.output_names == ("prepped",)
    assert prep.output_ports[0].type == "string"
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
        "nodes": [{"id": "a", "type": "agent", "run": "myscripts:prep"}],
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
    assert node_map["a"].input_names == ()
    assert node_map["b"].input_names == ("step_a_out",)
    assert node_map["c"].input_names == ("step_b_out",)


def test_load_tools_script_inputs_parsed() -> None:
    wf = load_workflow(FIXTURES / "tools_script.json")
    node_map = {n.id: n for n in wf.nodes}
    assert node_map["prep"].input_names == ("topic",)
    assert node_map["analyze"].input_names == ("prepped",)


def test_bad_unreachable_input_raises() -> None:
    # The fixture wires an edge into a port the destination never declares, so
    # the port mapping validation rejects it (the input stays unreachable).
    with pytest.raises(WorkflowValidationError, match="destination has no input port"):
        load_workflow(FIXTURES / "bad_unreachable_input.json")


def test_input_in_initial_state_validates_ok() -> None:
    data = {
        "name": "ok-initial",
        "provider": "anthropic",
        "entry": "a",
        "state": {"seed": "hello"},
        "nodes": [{"id": "a", "inputs": ["seed"]}],
        "edges": [{"from": "$in", "to": "a", "map": {"seed": "seed"}}],
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
    with pytest.raises(WorkflowValidationError, match="is not fed by any edge mapping"):
        validate_workflow(wf)


def test_two_unconditional_edges_into_one_port_raises() -> None:
    """Two producers feeding the same input port unconditionally is ambiguous.

    This is the clash the flat shared-state model used to allow silently (two
    nodes writing the same key, last-writer-wins by completion order).  The port
    model rejects it at validate time.
    """
    data = {
        "name": "ambiguous",
        "provider": "copilot",
        "entry": "p",
        "nodes": [
            {
                "id": "p",
                "type": "script",
                "code": "def p(ctx):\n    return 1",
                "outputs": [{"name": "z", "type": "integer"}],
            },
            {
                "id": "q",
                "type": "script",
                "code": "def q(ctx):\n    return 2",
                "outputs": [{"name": "z", "type": "integer"}],
            },
            {
                "id": "end",
                "type": "script",
                "inputs": [{"name": "v", "type": "integer"}],
                "code": "def end(ctx, v):\n    return v",
                "outputs": [{"name": "r", "type": "integer"}],
            },
        ],
        "edges": [
            {"from": "p", "to": "end", "map": {"z": "v"}},
            {"from": "q", "to": "end", "map": {"z": "v"}},
        ],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="ambiguous producer"):
        validate_workflow(wf)


def test_two_conditional_edges_into_one_port_ok() -> None:
    """Mutually-exclusive (conditional) producers into one port are allowed."""
    data = {
        "name": "cond-merge",
        "provider": "copilot",
        "entry": "route",
        "state": {"n": "1"},
        "nodes": [
            {
                "id": "route",
                "type": "script",
                "inputs": [{"name": "n", "type": "integer"}],
                "code": "def route(ctx, n):\n    return 'odd' if n % 2 else 'even'",
                "outputs": [{"name": "kind", "type": "string"}, {"name": "n_out", "type": "integer"}],
            },
            {
                "id": "odd",
                "type": "script",
                "inputs": [{"name": "x", "type": "integer"}],
                "code": "def odd(ctx, x):\n    return x",
                "outputs": [{"name": "z", "type": "integer"}],
            },
            {
                "id": "even",
                "type": "script",
                "inputs": [{"name": "x", "type": "integer"}],
                "code": "def even(ctx, x):\n    return x",
                "outputs": [{"name": "z", "type": "integer"}],
            },
            {
                "id": "merge",
                "type": "script",
                "inputs": [{"name": "v", "type": "integer"}],
                "code": "def merge(ctx, v):\n    return v",
                "outputs": [{"name": "out", "type": "integer"}],
            },
        ],
        "edges": [
            {"from": "$in", "to": "route", "map": {"n": "n"}},
            {"from": "$in", "to": "odd", "map": {"n": "x"}},
            {"from": "$in", "to": "even", "map": {"n": "x"}},
            {"from": "route", "to": "odd", "when": {"equals": {"text": "{{kind}}", "value": "odd"}}},
            {"from": "route", "to": "even", "when": {"equals": {"text": "{{kind}}", "value": "even"}}},
            # two conditional producers into merge.v — allowed (mutually exclusive)
            {"from": "odd", "to": "merge", "map": {"z": "v"}, "when": {"equals": {"text": "{{kind}}", "value": "odd"}}},
            {
                "from": "even",
                "to": "merge",
                "map": {"z": "v"},
                "when": {"equals": {"text": "{{kind}}", "value": "even"}},
            },
        ],
    }
    wf = parse_workflow(data)
    validate_workflow(wf)  # should not raise
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

    wf = parse_workflow(
        {
            "name": "t",
            "provider": "copilot",
            "entry": "add",
            "state": {"a": "3", "b": "4"},  # inputs reachable from initial state
            "nodes": [
                {
                    "id": "add",
                    "type": "script",
                    "code": "def add(ctx, a, b):\n    return a + b",
                    "inputs": [{"name": "a", "type": "integer"}, {"name": "b", "type": "integer"}],
                    "output": {"name": "sum", "type": "integer"},
                }
            ],
            "edges": [
                {"from": "$in", "to": "add", "map": {"a": "a", "b": "b"}},
            ],
        }
    )
    validate_workflow(wf)  # must not raise
    n = wf.nodes[0]
    assert n.input_names == ("a", "b")
    assert n.input_ports == (Port("a", "integer"), Port("b", "integer"))
    assert n.output_names == ("sum",)
    assert n.output_ports == (Port("sum", "integer"),)
    assert n.code is not None


def test_inline_bad_syntax_raises() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(
        _wf_with_script(
            {
                "id": "x",
                "type": "script",
                "code": "def x(ctx):\n    return (",
                "output": "y",
            }
        )
    )
    with pytest.raises(WorkflowValidationError, match="invalid code"):
        validate_workflow(wf)


def test_inline_first_param_must_be_ctx() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(
        _wf_with_script(
            {
                "id": "x",
                "type": "script",
                "code": "def x(a):\n    return a",
                "inputs": [{"name": "a", "type": "string"}],
                "output": "y",
            }
        )
    )
    with pytest.raises(WorkflowValidationError, match="first parameter must be 'ctx'"):
        validate_workflow(wf)


def test_inline_params_must_match_inputs() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(
        _wf_with_script(
            {
                "id": "x",
                "type": "script",
                "code": "def x(ctx, a, b):\n    return a",
                "inputs": [{"name": "a", "type": "string"}],
                "output": "y",
            }
        )
    )
    with pytest.raises(WorkflowValidationError, match="!= declared inputs"):
        validate_workflow(wf)


def test_script_code_and_run_both_set_raises() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(
        _wf_with_script(
            {
                "id": "x",
                "type": "script",
                "code": "def x(ctx):\n    return ''",
                "run": "m:f",
                "output": "y",
            }
        )
    )
    with pytest.raises(WorkflowValidationError, match="not both"):
        validate_workflow(wf)


def test_script_neither_code_nor_run_raises() -> None:
    from flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(_wf_with_script({"id": "x", "type": "script", "output": "y"}))
    with pytest.raises(WorkflowValidationError, match="must set 'code' or 'run'"):
        validate_workflow(wf)


# --- custom tool manifest ----------------------------------------------------


def _wf_with_tools(tools: dict[str, str], node_tools: list[str]) -> dict[str, object]:
    return {
        "name": "tw",
        "provider": "fake",
        "defaults": {"model": "m"},
        "entry": "a",
        "tools": tools,
        "nodes": [{"id": "a", "type": "agent", "model": "m", "prompt": "p", "tools": node_tools}],
        "edges": [],
    }


def test_parse_tool_manifest() -> None:
    wf = parse_workflow(_wf_with_tools({"reverse": "mytools:make_reverse"}, ["reverse"]))
    assert wf.tool_refs == (("reverse", "mytools:make_reverse"),)


def test_validate_manifest_tool_name_ok() -> None:
    wf = parse_workflow(_wf_with_tools({"reverse": "mytools:make_reverse"}, ["reverse"]))
    validate_workflow(wf)  # should not raise


def test_validate_builtin_tool_name_ok() -> None:
    wf = parse_workflow(_wf_with_tools({}, ["filesystem"]))
    validate_workflow(wf)  # built-in resolves without a manifest entry


def test_validate_unknown_tool_name_fails_fast() -> None:
    wf = parse_workflow(_wf_with_tools({"reverse": "mytools:make_reverse"}, ["revrese"]))
    with pytest.raises(WorkflowValidationError, match="unknown tool 'revrese'"):
        validate_workflow(wf)


def test_validate_bad_manifest_ref_syntax_fails() -> None:
    wf = parse_workflow(_wf_with_tools({"reverse": "not_a_ref"}, ["reverse"]))
    with pytest.raises(WorkflowValidationError, match="must match 'module.path:callable'"):
        validate_workflow(wf)


def test_validate_empty_manifest_name_fails() -> None:
    wf = parse_workflow(_wf_with_tools({"": "mytools:make_reverse"}, []))
    with pytest.raises(WorkflowValidationError, match="tool name must be non-empty"):
        validate_workflow(wf)
