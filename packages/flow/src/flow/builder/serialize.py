"""flow.builder.serialize — turn a WorkflowDef back into JSON.

This is the inverse of :func:`flow.loader.parse_workflow`.  The round-trip law
that guards it (see ``tests/test_serialize.py``) is::

    parse_workflow(workflow_to_dict(wf)) == wf

for every workflow.  A visual builder needs this: it loads JSON into the model,
lets the user edit, then writes runnable JSON back out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef


def _condition_to_dict(cond: Condition) -> dict[str, Any]:
    """Inverse of ``flow.loader._parse_condition``."""
    if cond.op in ("equals", "contains"):
        return {cond.op: {"value": cond.value, "text": cond.text}}
    if cond.op == "not":
        return {"not": _condition_to_dict(cond.children[0])}
    # and / or
    return {cond.op: [_condition_to_dict(c) for c in cond.children]}


def _node_to_dict(node: NodeDef) -> dict[str, Any]:
    """Inverse of ``flow.loader._parse_node`` — emit only non-default fields."""
    data: dict[str, Any] = {"id": node.id, "type": node.type}
    if node.model is not None:
        data["model"] = node.model
    if node.system_prompt:
        data["system_prompt"] = node.system_prompt
    if node.prompt:
        data["prompt"] = node.prompt
    if node.output is not None:
        # Typed output round-trips as {name, type}; bare output stays a string.
        if node.output_type is not None:
            data["output"] = {"name": node.output, "type": node.output_type}
        else:
            data["output"] = node.output
    if node.tools:
        data["tools"] = list(node.tools)
    if node.run is not None:
        data["run"] = node.run
    if node.code is not None:
        data["code"] = node.code
    if node.inputs:
        # Typed inputs round-trip as [{name, type}]; bare names stay strings.
        if node.input_schema:
            type_of = dict(node.input_schema)
            data["inputs"] = [{"name": n, "type": type_of.get(n, "string")} for n in node.inputs]
        else:
            data["inputs"] = list(node.inputs)
    if node.output_schema:
        data["output_schema"] = {k: v for k, v in node.output_schema}
    return data


def _edge_to_dict(edge: EdgeDef) -> dict[str, Any]:
    """Inverse of ``flow.loader._parse_edge``."""
    data: dict[str, Any] = {"from": edge.src, "to": edge.dst}
    if edge.when is not None:
        data["when"] = _condition_to_dict(edge.when)
    if edge.loop_max is not None:
        data["loop"] = {"max": edge.loop_max}
    return data


def workflow_to_dict(wf: WorkflowDef) -> dict[str, Any]:
    """Serialize a :class:`~flow.models.WorkflowDef` to a JSON-ready dict.

    The result parses back to an equal ``WorkflowDef`` via
    :func:`flow.loader.parse_workflow`.
    """
    data: dict[str, Any] = {
        "name": wf.name,
        "provider": wf.provider,
    }
    if wf.default_model:
        data["defaults"] = {"model": wf.default_model}
    data["entry"] = wf.entry
    if wf.initial_state:
        data["state"] = {k: v for k, v in wf.initial_state}
    data["nodes"] = [_node_to_dict(n) for n in wf.nodes]
    data["edges"] = [_edge_to_dict(e) for e in wf.edges]
    return data


def dump_workflow(wf: WorkflowDef, path: str | Path) -> None:
    """Write *wf* to *path* as pretty-printed, runnable workflow JSON."""
    p = Path(path)
    text = json.dumps(workflow_to_dict(wf), indent=2, ensure_ascii=False)
    p.write_text(text + "\n", encoding="utf-8")
