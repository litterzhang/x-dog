"""Tests for typed inline / ref script nodes (the (ctx, *inputs) convention)."""

from __future__ import annotations

import pathlib

from xdog.flow.executor import execute
from xdog.flow.loader import load_workflow, parse_workflow
from xdog.flow.models import NodeDef, WorkflowDef


def _run(wf: WorkflowDef, base_dir: pathlib.Path | None = None) -> dict[str, object]:
    import asyncio

    result = asyncio.run(execute(wf, base_dir=base_dir))
    # Flatten the nested real-node outputs into a single name->value view, the way
    # the old flat final_state read.
    flat: dict[str, object] = {}
    for ports in result.runtime["state"].values():
        flat.update(ports)
    return flat


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
            "inputs": [{"name": "a", "schema": {"type": "integer"}}, {"name": "b", "schema": {"type": "integer"}}],
            "outputs": [{"name": "sum", "schema": {"type": "integer"}}],
        }],
        "edges": [{"from": "$in", "to": "add", "map": {"a": "a", "b": "b"}}],
    })
    fs = _run(wf)
    # integer port is type-native: 3 + 4 = 7 (int), not "34" or "7"
    assert fs["sum"] == 7


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
            "inputs": [{"name": "x", "schema": {"type": "string"}}],
            "outputs": [{"name": "y", "schema": {"type": "string"}}],
        }],
        "edges": [{"from": "$in", "to": "s", "map": {"x": "x"}}],
    })
    assert _run(wf)["y"] == "HI"


def test_ctx_exposes_runtime_info() -> None:
    """ctx carries runtime info only (step/node_id/workflow_name), not inputs."""
    wf = parse_workflow({
        "name": "ctxwf",
        "provider": "copilot",
        "entry": "s",
        "state": {"seed": "z"},
        "nodes": [{
            "id": "s",
            "type": "script",
            "code": (
                "def s(ctx, seed):\n"
                "    return f'{ctx.workflow_name}/{ctx.node_id}/{ctx.step}/{seed}'"
            ),
            "inputs": [{"name": "seed", "schema": {"type": "string"}}],
            "outputs": [{"name": "out", "schema": {"type": "string"}}],
        }],
        "edges": [{"from": "$in", "to": "s", "map": {"seed": "seed"}}],
    })
    assert _run(wf)["out"] == "ctxwf/s/0/z"


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
            "outputs": [{"name": "d", "schema": {"type": "object"}}],
        }],
        "edges": [],
    })
    # object port keeps structure — a live dict, not a JSON string
    assert _run(wf)["d"] == {"a": 1, "b": 2}


def test_ref_imports_from_workflow_dir(tmp_path: pathlib.Path) -> None:
    """A `run: mod:func` script imports mod.py sitting next to the workflow."""
    (tmp_path / "myscript.py").write_text(
        "def greet(ctx, who):\n    return f'hello {who}'\n", encoding="utf-8"
    )
    wf_path = tmp_path / "wf.json"
    wf_path.write_text(
        '{"name":"r","provider":"copilot","entry":"g","state":{"who":"world"},'
        '"nodes":[{"id":"g","type":"script","run":"myscript:greet",'
        '"inputs":[{"name":"who","schema": {"type": "string"}}],'
        '"outputs": [{"name":"msg","schema": {"type": "string"}}]}],'
        '"edges":[{"from":"$in","to":"g","map":{"who":"who"}}]}',
        encoding="utf-8",
    )
    wf = load_workflow(wf_path)
    fs = _run(wf, base_dir=tmp_path)
    assert fs["msg"] == "hello world"


def test_nodedef_defaults_backcompat() -> None:
    n = NodeDef(id="x", type="script")
    assert n.code is None
    assert n.input_ports == ()
    assert n.input_names == ()
    assert n.output_ports == ()
    assert n.output_names == ()
