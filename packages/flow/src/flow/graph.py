"""flow.graph — ASCII and Mermaid rendering of WorkflowDef."""

from __future__ import annotations

from flow.models import Condition, EdgeDef, WorkflowDef


def _condition_label(cond: Condition) -> str:
    if cond.op == "equals":
        return f"equals:{cond.value}"
    if cond.op == "contains":
        return f"contains:{cond.value}"
    if cond.op == "not":
        child_label = _condition_label(cond.children[0]) if cond.children else ""
        return f"not({child_label})"
    if cond.op in ("and", "or"):
        parts = [_condition_label(c) for c in cond.children]
        return f" {cond.op} ".join(parts)
    return cond.op


def _edge_ascii(edge: EdgeDef) -> str:
    parts = [f"{edge.src} -> {edge.dst}"]
    annotations: list[str] = []
    if edge.when is not None:
        annotations.append(f"when: {_condition_label(edge.when)}")
    if edge.loop_max is not None:
        annotations.append(f"loop_max: {edge.loop_max}")
    if annotations:
        parts.append(f"[{', '.join(annotations)}]")
    return " ".join(parts)


def to_ascii(wf: WorkflowDef) -> str:
    """Return a human-readable node/edge listing for *wf*."""
    lines: list[str] = [f"workflow: {wf.name}", f"entry: {wf.entry}", "nodes:"]
    for node in wf.nodes:
        lines.append(f"  {node.id}")
    lines.append("edges:")
    for edge in wf.edges:
        lines.append(f"  {_edge_ascii(edge)}")
    return "\n".join(lines)


def _mermaid_edge(edge: EdgeDef) -> str:
    if edge.when is not None:
        label = _condition_label(edge.when)
        return f"    {edge.src} -- {label} --> {edge.dst}"
    if edge.loop_max is not None:
        return f"    {edge.src} -- loop --> {edge.dst}"
    return f"    {edge.src} --> {edge.dst}"


def to_mermaid(wf: WorkflowDef) -> str:
    """Return a Mermaid ``flowchart TD`` block for *wf*."""
    lines: list[str] = ["flowchart TD"]
    for node in wf.nodes:
        lines.append(f"    {node.id}[{node.id}]")
    for edge in wf.edges:
        lines.append(_mermaid_edge(edge))
    return "\n".join(lines)
