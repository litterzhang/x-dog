"""flow.graph — ASCII, Mermaid, and SVG rendering of WorkflowDef."""

from __future__ import annotations

import shutil
from xml.sax.saxutils import escape

from flow.models import IN_NODE_ID, Condition, EdgeDef, WorkflowDef


def _condition_label(cond: Condition) -> str:
    if cond.op == "equals":
        return f"equals:{cond.value}"
    if cond.op == "contains":
        return f"contains:{cond.value}"
    if cond.op in ("gt", "gte", "lt", "lte"):
        return f"{cond.op}:{cond.value} {cond.text}"
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
    if edge.mapping:
        annotations.append("map: " + ", ".join(f"{s}->{d}" for s, d in edge.mapping))
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


def _port_label(edge: EdgeDef) -> str:
    """Short label for the data an edge carries: its port mapping (+ when/loop)."""
    parts: list[str] = []
    if edge.mapping:
        parts.append(", ".join(f"{s}→{d}" if s != d else s for s, d in edge.mapping))
    if edge.when is not None:
        parts.append(_condition_label(edge.when))
    if edge.loop_max is not None:
        parts.append(f"loop≤{edge.loop_max}")
    return " ".join(parts)


# Direction bit-mask -> box-drawing glyph, so crossing lane segments merge into
# the correct junction (┼ ├ ┤ ┬ ┴) instead of overwriting one another.
_N, _S, _E, _W = 1, 2, 4, 8
_MASK_GLYPH: dict[int, str] = {
    0: " ",
    _N: "│", _S: "│", _N | _S: "│",
    _E: "─", _W: "─", _E | _W: "─",
    _S | _E: "┌", _S | _W: "┐", _N | _E: "└", _N | _W: "┘",
    _N | _S | _E: "├", _N | _S | _W: "┤", _S | _E | _W: "┬", _N | _E | _W: "┴",
    _N | _S | _E | _W: "┼",
}


def _layers(wf: WorkflowDef) -> tuple[dict[str, int], list[EdgeDef], list[EdgeDef]]:
    """Longest-path topological layering of *wf*'s real nodes.

    Returns ``(layer_of_node, forward_edges, loop_edges)``.  ``$in`` edges are
    ignored; loop back-edges (``loop_max`` set) do not constrain layers and are
    returned separately for lane routing.
    """
    order = {node.id: i for i, node in enumerate(wf.nodes)}
    forward = [
        e
        for e in wf.edges
        if e.src != IN_NODE_ID and e.src in order and e.dst in order and e.loop_max is None
    ]
    loops = [e for e in wf.edges if e.src in order and e.dst in order and e.loop_max is not None]
    preds: dict[str, list[str]] = {node.id: [] for node in wf.nodes}
    for e in forward:
        preds[e.dst].append(e.src)

    layer: dict[str, int] = {}

    def depth_of(nid: str) -> int:
        if nid in layer:
            return layer[nid]
        layer[nid] = 0 if not preds[nid] else 1 + max(depth_of(p) for p in preds[nid])
        return layer[nid]

    for node in wf.nodes:
        depth_of(node.id)
    return layer, forward, loops


def to_ascii_diagram(wf: WorkflowDef) -> str:
    """Return a boxed ASCII flow diagram with a layered, top-down layout.

    Nodes are grouped into topological layers (longest-path); the layers stack
    vertically and **parallel nodes in the same layer are placed side by side**,
    so the drawing reflects the graph's real fan-out/fan-in structure.  An edge
    between adjacent layers is routed inline as a ``│``/``▼`` orthogonal drop
    labelled with its ports.  A *skip* edge (spanning more than one layer) or a
    loop back-edge is routed through a vertical *lane* on the right, turning into
    the destination with a ``◄`` arrow and a ``src→dst [ports]`` (or ``src↺dst``)
    label.  Edges from the reserved ``$in`` source are omitted.  Deterministic and
    dependency-free.
    """
    nodes = wf.nodes
    if not nodes:
        return "(empty workflow)"

    layer, forward, loops = _layers(wf)
    from collections import defaultdict

    by_layer: dict[int, list[str]] = defaultdict(list)
    for node in nodes:  # declaration order within each layer (deterministic)
        by_layer[layer[node.id]].append(node.id)
    depth = max(by_layer) + 1

    labels: dict[str, str] = {}
    for node in nodes:
        lbl = f" {node.id} [{node.type}]"
        if node.id == wf.entry:
            lbl += " *"
        labels[node.id] = lbl
    box_w = {nid: len(labels[nid]) + 2 for nid in labels}

    band = 5  # 3 box rows + 2 routing rows below each layer
    col_gap = 3

    # Left-aligned side-by-side placement within each layer.
    x: dict[str, int] = {}
    max_layer_w = 0
    for lv in range(depth):
        cx = 0
        for nid in by_layer[lv]:
            x[nid] = cx
            cx += box_w[nid] + col_gap
        max_layer_w = max(max_layer_w, cx - col_gap if by_layer[lv] else 0)
    spine_w = max_layer_w + 2

    # Skip-forward (layer distance > 1) and loop edges route on right-side lanes.
    lane_edges = [e for e in forward if layer[e.dst] - layer[e.src] > 1] + loops
    n_lanes = len(lane_edges)

    def lane_x(i: int) -> int:
        return spine_w + 2 + i * 3

    lane_col = {id(e): lane_x(i) for i, e in enumerate(lane_edges)}
    label_col = lane_x(n_lanes) + 1 if n_lanes else spine_w + 2
    width = label_col + 2
    for e in lane_edges:
        width = max(width, label_col + len(f"{e.src}→{e.dst}") + len(_port_label(e)) + 6)
    total_rows = depth * band + 1

    def y_top(lv: int) -> int:
        return lv * band

    mask = [[0] * width for _ in range(total_rows)]
    text: list[list[str | None]] = [[None] * width for _ in range(total_rows)]

    def add(r: int, c: int, bits: int) -> None:
        if 0 <= r < total_rows and 0 <= c < width:
            mask[r][c] |= bits

    def put(r: int, c: int, s: str) -> None:
        for i, ch in enumerate(s):
            if 0 <= r < total_rows and 0 <= c + i < width:
                text[r][c + i] = ch

    def box_cx(nid: str) -> int:
        return x[nid] + box_w[nid] // 2

    def box_bottom(nid: str) -> int:
        return y_top(layer[nid]) + 2

    def box_top(nid: str) -> int:
        return y_top(layer[nid])

    def box_mid(nid: str) -> int:
        return y_top(layer[nid]) + 1

    def box_right(nid: str) -> int:
        return x[nid] + box_w[nid] - 1

    # Boxes.
    for nid in labels:
        left = x[nid]
        w = box_w[nid]
        t = box_top(nid)
        add(t, left, _S | _E)
        for c in range(left + 1, left + w - 1):
            add(t, c, _E | _W)
        add(t, left + w - 1, _S | _W)
        add(t + 1, left, _N | _S)
        add(t + 1, left + w - 1, _N | _S)
        put(t + 1, left + 1, labels[nid])
        add(t + 2, left, _N | _E)
        for c in range(left + 1, left + w - 1):
            add(t + 2, c, _E | _W)
        add(t + 2, left + w - 1, _N | _W)

    # Adjacent forward edges: orthogonal drop through the routing channel.
    for e in forward:
        if layer[e.dst] - layer[e.src] > 1:
            continue
        sc, dc = box_cx(e.src), box_cx(e.dst)
        r0 = box_bottom(e.src)
        r1 = box_top(e.dst)
        chan = r1 - 1  # routing row just above the destination band
        add(r0, sc, _S)
        for r in range(r0 + 1, chan):
            add(r, sc, _N | _S)
        add(chan, sc, _N | (_E if dc > sc else _W if dc < sc else _S))
        lo, hi = sorted((sc, dc))
        for c in range(lo + 1, hi):
            add(chan, c, _E | _W)
        if dc != sc:
            add(chan, dc, _S | (_W if dc > sc else _E))
        else:
            add(chan, dc, _N | _S)
        add(r1, dc, _N | _S)
        put(r1, dc, "▼")
        lbl = _port_label(e)
        if lbl:
            put(r0 + 1, sc + 2, lbl)

    # Lane-routed edges: source right side -> vertical lane -> destination ◄,
    # then a leader east to an aligned label column.
    for e in lane_edges:
        col = lane_col[id(e)]
        sr, tr = box_mid(e.src), box_mid(e.dst)
        down = layer[e.dst] > layer[e.src]
        for c in range(box_right(e.src) + 1, col):
            add(sr, c, _E | _W)
        add(sr, col, _W | (_S if down else _N))
        for r in range(min(sr, tr) + 1, max(sr, tr)):
            add(r, col, _N | _S)
        add(tr, col, _W | (_N if down else _S))
        for c in range(box_right(e.dst) + 1, col):
            add(tr, c, _E | _W)
        put(tr, box_right(e.dst) + 1, "◄")
        add(tr, col, _E)
        for c in range(col + 1, label_col):
            add(tr, c, _E | _W)
        lbl = _port_label(e)
        arrow = "↺" if e.loop_max is not None else "→"
        put(tr, label_col, f"{e.src}{arrow}{e.dst}" + (f" [{lbl}]" if lbl else ""))

    out: list[str] = []
    for r in range(total_rows):
        line: list[str] = []
        for c in range(width):
            ch = text[r][c]
            line.append(ch if ch is not None else _MASK_GLYPH[mask[r][c]])
        out.append("".join(line).rstrip())
    return "\n".join(out)


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
            node_label = f"{node.id}\\n[{node.type}]" + ("  *" if node.id == wf.entry else "")
            graph.add_node(
                pydot.Node(
                    name,
                    shape="box",
                    style="rounded,filled",
                    fillcolor=fillcolor,
                    color="#333333",
                    penwidth="1",
                    label=node_label,
                )
            )

        for edge in wf.edges:
            if edge.src not in known or edge.dst not in known:
                continue
            src = edge.src if edge.src.isidentifier() else f'"{edge.src}"'
            dst = edge.dst if edge.dst.isidentifier() else f'"{edge.dst}"'
            label = _port_label(edge)
            if label:
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
        type_label = f"[{node.type}]" + ("  *" if node.id == wf.entry else "")
        parts.append(
            f'<text x="{cx}" y="{cy - 6}" text-anchor="middle" dominant-baseline="central" '
            f'font-family="sans-serif" font-size="14">{escape(node.id)}</text>'
        )
        parts.append(
            f'<text x="{cx}" y="{cy + 12}" text-anchor="middle" dominant-baseline="central" '
            f'font-family="sans-serif" font-size="10" fill="#666">{escape(type_label)}</text>'
        )

    for edge in wf.edges:
        if edge.src not in centers or edge.dst not in centers:
            continue
        sx, sy = centers[edge.src]
        dx, dy = centers[edge.dst]
        back = index[edge.dst] <= index[edge.src]
        label = _port_label(edge)
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
        if label:
            parts.append(
                f'<text x="{lx}" y="{ly}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="11" fill="#666">{escape(label)}</text>'
            )

    parts.append("</svg>")
    return "".join(parts)
