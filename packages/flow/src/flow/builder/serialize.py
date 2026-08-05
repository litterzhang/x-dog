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

from flow.models import Condition, EdgeDef, NodeDef, Port, ScheduleDef, WorkflowDef


def _condition_to_dict(cond: Condition) -> dict[str, Any]:
    """Inverse of ``flow.loader._parse_condition``."""
    if cond.op in ("equals", "contains", "gt", "gte", "lt", "lte"):
        return {cond.op: {"value": cond.value, "text": cond.text}}
    if cond.op == "not":
        return {"not": _condition_to_dict(cond.children[0])}
    # and / or
    return {cond.op: [_condition_to_dict(c) for c in cond.children]}


def _ports_to_json(ports: tuple[Port, ...]) -> list[Any]:
    """Emit bare required strings or canonical ``schema``/``required`` objects."""
    out: list[Any] = []
    for port in ports:
        if port.schema == {"type": "string"} and port.required:
            out.append(port.name)
        else:
            out.append(
                {
                    "name": port.name,
                    "schema": port.schema,
                    "required": port.required,
                }
            )
    return out


def _node_to_dict(node: NodeDef) -> dict[str, Any]:
    """Inverse of ``flow.loader._parse_node`` — emit only non-default fields."""
    data: dict[str, Any] = {"id": node.id, "type": node.type}
    if node.signal:
        data["signal"] = node.signal
    if node.model is not None:
        data["model"] = node.model
    if node.system_prompt:
        data["system_prompt"] = node.system_prompt
    if node.prompt:
        data["prompt"] = node.prompt
    if node.tools:
        data["tools"] = list(node.tools)
    if node.run is not None:
        data["run"] = node.run
    if node.code is not None:
        data["code"] = node.code
    if node.retry is not None:
        data["retry"] = {"max": node.retry.max, "backoff": node.retry.backoff}
    if node.on_error != "fail":
        data["on_error"] = node.on_error
    if node.deterministic:
        data["deterministic"] = True
    if node.type == "subflow":
        # A subflow node carries its INLINE child; its ports are DERIVED on re-parse,
        # so they are NOT emitted (re-declaring them would fail validation).
        if node.child is not None:
            data["subflow"] = workflow_to_dict(node.child)
        return data
    if node.input_ports:
        data["inputs"] = _ports_to_json(node.input_ports)
    if node.output_ports:
        data["outputs"] = _ports_to_json(node.output_ports)
    if node.web_search:
        data["web_search"] = True
    if node.web_search_model is not None:
        data["web_search_model"] = node.web_search_model
    if node.backend is not None:
        data["backend"] = node.backend
    if node.allowed_tools:
        data["allowed_tools"] = list(node.allowed_tools)
    if node.mcp_servers:
        data["mcp_servers"] = {name: spec for name, spec in node.mcp_servers}
    return data


def _edge_to_dict(edge: EdgeDef) -> dict[str, Any]:
    """Inverse of ``flow.loader._parse_edge``."""
    data: dict[str, Any] = {"from": edge.src, "to": edge.dst}
    if edge.mapping:
        data["map"] = {s: d for s, d in edge.mapping}
    if edge.loop_strict:
        # A strict ``while`` loop is authored sugar: re-emit the long form so the
        # round-trip law (parse(to_dict(wf)) == wf) preserves loop_strict.  ``when``
        # carries the loop condition and ``loop_max`` the bound.
        cond = _condition_to_dict(edge.when) if edge.when is not None else {}
        data["while"] = {"cond": cond, "max": edge.loop_max}
    else:
        if edge.when is not None:
            data["when"] = _condition_to_dict(edge.when)
        if edge.loop_max is not None:
            data["loop"] = {"max": edge.loop_max}
    if edge.fan_out is not None:
        data["fan_out"] = edge.fan_out
    if edge.fan_in is not None:
        data["fan_in"] = edge.fan_in
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
    if wf.entry:
        data["entry"] = wf.entry
    if wf.tool_refs:
        data["tools"] = {name: ref for name, ref in wf.tool_refs}
    if wf.initial_state:
        data["state"] = {k: v for k, v in wf.initial_state}
    if wf.in_schema:
        data["in_schema"] = {k: v for k, v in wf.in_schema}
    if wf.max_concurrency:
        data["max_concurrency"] = wf.max_concurrency
    if wf.fan_max_concurrency:
        data["fan_max_concurrency"] = wf.fan_max_concurrency
    if wf.schedule is not None:
        data["schedule"] = _schedule_to_dict(wf.schedule)
    data["nodes"] = [_node_to_dict(n) for n in wf.nodes]
    data["edges"] = [_edge_to_dict(e) for e in wf.edges]
    return data


def _schedule_to_dict(sch: ScheduleDef) -> dict[str, Any]:
    """Inverse of ``flow.loader._parse_schedule``."""
    out: dict[str, Any] = {"mode": sch.mode}
    if sch.every is not None:
        out["every"] = sch.every
    if sch.cron is not None:
        out["cron"] = sch.cron
    if sch.inputs:
        out["inputs"] = {k: v for k, v in sch.inputs}
    if sch.signal is not None:
        out["signal"] = sch.signal
    if sch.listen is not None:
        out["listen"] = sch.listen
    return out


def dump_workflow(wf: WorkflowDef, path: str | Path) -> None:
    """Write *wf* to *path* as pretty-printed, runnable workflow JSON."""
    p = Path(path)
    text = json.dumps(workflow_to_dict(wf), indent=2, ensure_ascii=False)
    p.write_text(text + "\n", encoding="utf-8")
