"""flow.graph — ASCII, Mermaid, and SVG rendering of WorkflowDef."""

from __future__ import annotations

import shutil
from xml.sax.saxutils import escape

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


_MARGIN = 20
_BOX_W = 160
_BOX_H = 50
_GAP = 40
_LANE = 80


def _edge_label(edge: EdgeDef) -> str | None:
    if edge.when is not None:
        return _condition_label(edge.when)
    if edge.loop_max is not None:
        return "loop"
    return None


def to_svg(wf: WorkflowDef) -> str:
    """Return an SVG document rendering *wf*.

    Uses Graphviz (via pydot + the system ``dot`` binary) for auto-layout when
    available, and falls back to a deterministic hand-assembled renderer
    otherwise.  (During migration this simply delegates to the fallback; the
    pydot path is generated into this function by the codegen workflow.)
    """
    if shutil.which("dot") is None:
        return _to_svg_fallback(wf)
    try:
        import pydot

        graph = pydot.Dot(graph_type="digraph", rankdir="TB")
        graph.set_graph_defaults(fontname="sans-serif")
        graph.set_node_defaults(fontname="sans-serif", fontsize="14")
        graph.set_edge_defaults(fontname="sans-serif", fontsize="11", color="#333333")

        known: set[str] = set()
        for node in wf.nodes:
            known.add(node.id)
            fillcolor = "#e8f0fe" if node.type == "agent" else "#f5f5f5"
            name = node.id if node.id.isidentifier() else f'"{node.id}"'
            graph.add_node(
                pydot.Node(
                    name,
                    shape="box",
                    style="rounded,filled",
                    fillcolor=fillcolor,
                    color="#333333",
                    penwidth="1",
                    label=node.id,
                )
            )

        for edge in wf.edges:
            if edge.src not in known or edge.dst not in known:
                continue
            src = edge.src if edge.src.isidentifier() else f'"{edge.src}"'
            dst = edge.dst if edge.dst.isidentifier() else f'"{edge.dst}"'
            label = _edge_label(edge)
            if label is not None:
                graph.add_edge(pydot.Edge(src, dst, label=label))
            else:
                graph.add_edge(pydot.Edge(src, dst))

        raw_bytes: bytes = graph.create_svg()  # type: ignore[attr-defined]
        raw = raw_bytes.decode("utf-8")
        start = raw.find("<svg")
        if start == -1:
            return _to_svg_fallback(wf)
        return raw[start:]
    except Exception:
        return _to_svg_fallback(wf)


def _to_svg_fallback(wf: WorkflowDef) -> str:
    """Deterministic, dependency-free SVG renderer (no Graphviz required)."""
    index: dict[str, int] = {node.id: i for i, node in enumerate(wf.nodes)}
    centers: dict[str, tuple[float, float]] = {}
    positions: dict[str, tuple[float, float]] = {}
    for node in wf.nodes:
        i = index[node.id]
        x = float(_MARGIN)
        y = float(_MARGIN + i * (_BOX_H + _GAP))
        positions[node.id] = (x, y)
        centers[node.id] = (x + _BOX_W / 2, y + _BOX_H / 2)

    n = len(wf.nodes)
    width = _MARGIN * 2 + _BOX_W + _LANE
    height = _MARGIN * 2 + max(0, n * (_BOX_H + _GAP) - _GAP)

    parts: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    )
    parts.append(
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#333"/></marker></defs>'
    )

    for node in wf.nodes:
        x, y = positions[node.id]
        cx, cy = centers[node.id]
        parts.append(f'<rect x="{x}" y="{y}" width="{_BOX_W}" height="{_BOX_H}" rx="6" fill="#f5f5f5" stroke="#333"/>')
        parts.append(
            f'<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central" '
            f'font-family="sans-serif" font-size="14">{escape(node.id)}</text>'
        )

    for edge in wf.edges:
        if edge.src not in centers or edge.dst not in centers:
            continue
        sx, sy = centers[edge.src]
        dx, dy = centers[edge.dst]
        back = index[edge.dst] <= index[edge.src]
        label = _edge_label(edge)
        if back:
            lane_x = _MARGIN + _BOX_W + _LANE / 2
            src_x = positions[edge.src][0] + _BOX_W
            dst_x = positions[edge.dst][0] + _BOX_W
            parts.append(
                f'<path d="M{src_x},{sy} C{lane_x},{sy} {lane_x},{dy} {dst_x},{dy}" '
                f'fill="none" stroke="#333" marker-end="url(#arrow)"/>'
            )
            lx, ly = lane_x, (sy + dy) / 2
        else:
            y1 = positions[edge.src][1] + _BOX_H
            y2 = positions[edge.dst][1]
            parts.append(f'<line x1="{sx}" y1="{y1}" x2="{dx}" y2="{y2}" stroke="#333" marker-end="url(#arrow)"/>')
            lx, ly = (sx + dx) / 2, (y1 + y2) / 2
        if label is not None:
            parts.append(
                f'<text x="{lx}" y="{ly}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="11" fill="#666">{escape(label)}</text>'
            )

    parts.append("</svg>")
    return "".join(parts)
