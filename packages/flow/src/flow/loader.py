"""flow.loader — load and validate WorkflowDef from JSON."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from flow.errors import WorkflowValidationError
from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef

logger = logging.getLogger(__name__)


def _parse_condition(data: Any) -> Condition:
    if not isinstance(data, dict):
        raise WorkflowValidationError(f"Condition must be a dict, got {type(data)}")
    if "equals" in data:
        inner = data["equals"]
        return Condition(op="equals", value=str(inner["value"]), text=str(inner["text"]))
    if "contains" in data:
        inner = data["contains"]
        return Condition(op="contains", value=str(inner["value"]), text=str(inner["text"]))
    if "not" in data:
        return Condition(op="not", children=(_parse_condition(data["not"]),))
    if "and" in data:
        return Condition(op="and", children=tuple(_parse_condition(c) for c in data["and"]))
    if "or" in data:
        return Condition(op="or", children=tuple(_parse_condition(c) for c in data["or"]))
    raise WorkflowValidationError(f"Unknown condition keys: {list(data.keys())}")


def _parse_node(data: dict[str, Any]) -> NodeDef:
    return NodeDef(
        id=str(data["id"]),
        type=data.get("type", "agent"),
        model=data.get("model"),
        system_prompt=data.get("system_prompt", ""),
        prompt=data.get("prompt", ""),
        output=data.get("output"),
    )


def _parse_edge(data: dict[str, Any]) -> EdgeDef:
    when: Condition | None = None
    if "when" in data:
        when = _parse_condition(data["when"])
    loop_max: int | None = None
    if "loop" in data and isinstance(data["loop"], dict):
        loop_max = int(data["loop"]["max"])
    return EdgeDef(
        src=str(data["from"]),
        dst=str(data["to"]),
        when=when,
        loop_max=loop_max,
    )


def parse_workflow(data: dict[str, Any]) -> WorkflowDef:
    """Build a WorkflowDef from a raw dict (already parsed from JSON)."""
    name = str(data.get("name", ""))
    provider = str(data.get("provider", ""))
    entry = str(data.get("entry", ""))
    default_model = str(data.get("defaults", {}).get("model", ""))

    raw_state = data.get("state", {})
    initial_state: tuple[tuple[str, str], ...]
    if isinstance(raw_state, dict):
        initial_state = tuple((k, str(v)) for k, v in raw_state.items())
    else:
        initial_state = ()

    nodes = tuple(_parse_node(n) for n in data.get("nodes", []))
    edges = tuple(_parse_edge(e) for e in data.get("edges", []))

    return WorkflowDef(
        name=name,
        provider=provider,
        entry=entry,
        nodes=nodes,
        edges=edges,
        default_model=default_model,
        initial_state=initial_state,
    )


def validate_workflow(wf: WorkflowDef) -> None:
    """Validate a WorkflowDef. Raises WorkflowValidationError on any problem."""
    node_ids = [n.id for n in wf.nodes]

    # node ids non-empty and unique
    for nid in node_ids:
        if not nid:
            raise WorkflowValidationError("Node id must be non-empty")
    if len(node_ids) != len(set(node_ids)):
        raise WorkflowValidationError(f"Duplicate node ids: {node_ids}")

    node_index: dict[str, int] = {nid: i for i, nid in enumerate(node_ids)}

    # entry exists
    if wf.entry not in node_index:
        raise WorkflowValidationError(f"Entry node {wf.entry!r} not found in nodes")

    # edges reference existing nodes; back-edges need loop_max
    for edge in wf.edges:
        if edge.src not in node_index:
            raise WorkflowValidationError(f"Edge src {edge.src!r} not found in nodes")
        if edge.dst not in node_index:
            raise WorkflowValidationError(f"Edge dst {edge.dst!r} not found in nodes")
        # back-edge: dst appears earlier than src (would form a cycle)
        if node_index[edge.dst] <= node_index[edge.src]:
            if not (edge.loop_max is not None and edge.loop_max >= 1):
                raise WorkflowValidationError(f"Back-edge {edge.src!r} -> {edge.dst!r} must have loop.max >= 1")


def load_workflow(path: str | Path) -> WorkflowDef:
    """Load a WorkflowDef from a JSON file at *path*."""
    p = Path(path)
    logger.debug("Loading workflow from %s", p)
    with p.open() as fh:
        data: dict[str, Any] = json.load(fh)
    wf = parse_workflow(data)
    validate_workflow(wf)
    return wf
