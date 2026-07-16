"""Tests for typed inline / ref script nodes (the (ctx, *inputs) convention)."""

from __future__ import annotations

import pathlib

from flow.executor import execute
from flow.loader import load_workflow, parse_workflow
from flow.models import NodeDef, WorkflowDef


def _run(wf: WorkflowDef, base_dir: pathlib.Path | None = None) -> dict[str, str]:
    import asyncio

    return asyncio.run(execute(wf, base_dir=base_dir)).final_state


def test_inline_typed_add_coerces() -> None:
    """def add(ctx, a, b) with integer inputs computes 3+4=7, not '34'."""
    wf = parse_workflow({
        "name": "adder",
        "provider": "copilot",
        "entry": "add",
        "state": {"a": "3", "b": "4"},
        "nodes": [{
            "id": "add",
            "type": "script",
            "code": "def add(ctx, a, b):\n    return a + b",
            "inputs": [{"name": "a", "type": "integer"}, {"name": "b", "type": "integer"}],
            "output": {"name": "sum", "type": "integer"},
        }],
        "edges": [],
    })
    fs = _run(wf)
    assert fs["sum"] == "7"


def test_inline_async_script() -> None:
    wf = parse_workflow({
        "name": "a",
        "provider": "copilot",
        "entry": "s",
        "state": {"x": "hi"},
        "nodes": [{
            "id": "s",
            "type": "script",
            "code": "async def s(ctx, x):\n    return x.upper()",
            "inputs": [{"name": "x", "type": "string"}],
            "output": {"name": "y", "type": "string"},
        }],
        "edges": [],
    })
    assert _run(wf)["y"] == "HI"


def test_ctx_exposes_state_and_ids() -> None:
    wf = parse_workflow({
        "name": "ctxwf",
        "provider": "copilot",
        "entry": "s",
        "state": {"seed": "z"},
        "nodes": [{
            "id": "s",
            "type": "script",
            "code": (
                "def s(ctx):\n"
                "    return f'{ctx.workflow_name}/{ctx.node_id}/{ctx.state[\"seed\"]}'"
            ),
            "output": {"name": "out", "type": "string"},
        }],
        "edges": [],
    })
    assert _run(wf)["out"] == "ctxwf/s/z"


def test_object_output_serialized() -> None:
    wf = parse_workflow({
        "name": "obj",
        "provider": "copilot",
        "entry": "s",
        "state": {},
        "nodes": [{
            "id": "s",
            "type": "script",
            "code": "def s(ctx):\n    return {'a': 1, 'b': 2}",
            "output": {"name": "d", "type": "object"},
        }],
        "edges": [],
    })
    import json

    assert json.loads(_run(wf)["d"]) == {"a": 1, "b": 2}


def test_ref_imports_from_workflow_dir(tmp_path: pathlib.Path) -> None:
    """A `run: mod:func` script imports mod.py sitting next to the workflow."""
    (tmp_path / "myscript.py").write_text(
        "def greet(ctx, who):\n    return f'hello {who}'\n", encoding="utf-8"
    )
    wf_path = tmp_path / "wf.json"
    wf_path.write_text(
        '{"name":"r","provider":"copilot","entry":"g","state":{"who":"world"},'
        '"nodes":[{"id":"g","type":"script","run":"myscript:greet",'
        '"inputs":[{"name":"who","type":"string"}],'
        '"output":{"name":"msg","type":"string"}}],"edges":[]}',
        encoding="utf-8",
    )
    wf = load_workflow(wf_path)
    fs = _run(wf, base_dir=tmp_path)
    assert fs["msg"] == "hello world"


def test_nodedef_defaults_backcompat() -> None:
    n = NodeDef(id="x", type="script")
    assert n.code is None
    assert n.input_schema == ()
    assert n.output_type is None
