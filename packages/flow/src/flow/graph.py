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


def to_ascii_diagram(wf: WorkflowDef) -> str:
    """Return a boxed ASCII flow diagram with 2-D routed edges.

    Nodes are drawn top-to-bottom in a left-aligned *spine*.  A plain edge to the
    very next node is a simple ``│``/``▼`` down-arrow labelled with its ports.
    Any other real edge (a skip, a branch, or a loop back-edge) is routed through
    a vertical *lane* on the right: it leaves the source box, travels down (or up,
    for loops) its own column, and turns into the destination box with a ``◄``
    arrow, with the ``src→dst [ports]`` label printed alongside.  Lanes are packed
    so non-overlapping edges share a column.  Edges from the reserved ``$in``
    source are omitted (they feed nearly every node and only add noise).
    Deterministic and dependency-free.
    """
    nodes = wf.nodes
    if not nodes:
        return "(empty workflow)"

    order = {node.id: i for i, node in enumerate(nodes)}
    n = len(nodes)
    row_h = 5  # rows per node: top, mid, bottom, +2 gap

    def top(i: int) -> int:
        return i * row_h

    def mid(i: int) -> int:
        return i * row_h + 1

    def bot(i: int) -> int:
        return i * row_h + 2

    labels: list[str] = []
    for node in nodes:
        lbl = f" {node.id} [{node.type}]"
        if node.id == wf.entry:
            lbl += " *"
        labels.append(lbl)
    box_w = [len(lbl) + 2 for lbl in labels]  # incl. the two vertical borders
    spine = max(box_w)

    # Classify real (non-$in, in-graph) edges into sequential vs lane-routed.
    real = [e for e in wf.edges if e.src != IN_NODE_ID and e.src in order and e.dst in order]
    seq_edges: list[EdgeDef] = []
    lane_edges: list[EdgeDef] = []
    for e in real:
        si, di = order[e.src], order[e.dst]
        if di == si + 1 and e.when is None and e.loop_max is None:
            seq_edges.append(e)
        else:
            lane_edges.append(e)

    def row_span(e: EdgeDef) -> tuple[int, int]:
        a, b = mid(order[e.src]), mid(order[e.dst])
        return (min(a, b), max(a, b))

    # Nested-lane packing: longer spans take inner columns (closer to the spine)
    # and shorter spans nest outside them.  This keeps the edge turning in on any
    # given row the right-most thing there, so its ◄ corner and inline label never
    # sit behind an outer lane.  Longest-first; each edge takes the first column
    # whose occupied intervals stay disjoint.  Deterministic.
    def _span_len(e: EdgeDef) -> int:
        s, t = row_span(e)
        return t - s

    keyed = sorted(
        range(len(lane_edges)),
        key=lambda i: (-_span_len(lane_edges[i]), row_span(lane_edges[i])[0]),
    )
    lanes: list[list[tuple[int, int]]] = []
    lane_col: dict[int, int] = {}
    for idx in keyed:
        s, t = row_span(lane_edges[idx])
        placed = False
        for j, occupied in enumerate(lanes):
            if all(t < a or s > b for a, b in occupied):
                occupied.append((s, t))
                lane_col[idx] = j
                placed = True
                break
        if not placed:
            lanes.append([(s, t)])
            lane_col[idx] = len(lanes) - 1
    n_lanes = len(lanes)

    lane_base = spine + 2

    def lane_x(k: int) -> int:
        return lane_base + k * 3

    def _tag(e: EdgeDef) -> str:
        lbl = _port_label(e)
        return f"{e.src}→{e.dst}" + (f" [{lbl}]" if lbl else "")

    # A single label column to the right of every lane.  Each edge's corner is
    # joined to its label by a horizontal leader, so labels stay aligned and
    # visibly connected (never floating past an intervening lane).  Edges that
    # share a destination share a row, so their tags are joined for that row.
    label_col = lane_x(n_lanes - 1) + 2 if n_lanes else spine + 2
    row_tags: dict[int, list[str]] = {}
    for idx, e in enumerate(lane_edges):
        row_tags.setdefault(mid(order[e.dst]), []).append(_tag(e))
    row_label = {r: "  ".join(tags) for r, tags in row_tags.items()}

    total_rows = (n - 1) * row_h + 3
    max_label = max((len(s) for s in row_label.values()), default=0)
    width = label_col + max_label + 2

    mask = [[0] * width for _ in range(total_rows)]
    text: list[list[str | None]] = [[None] * width for _ in range(total_rows)]

    def add(r: int, c: int, bits: int) -> None:
        if 0 <= r < total_rows and 0 <= c < width:
            mask[r][c] |= bits

    def put(r: int, c: int, s: str) -> None:
        for i, ch in enumerate(s):
            if 0 <= r < total_rows and 0 <= c + i < width:
                text[r][c + i] = ch

    # Boxes.
    for i, lbl in enumerate(labels):
        w = box_w[i]
        t, m, b = top(i), mid(i), bot(i)
        add(t, 0, _S | _E)
        for c in range(1, w - 1):
            add(t, c, _E | _W)
        add(t, w - 1, _S | _W)
        add(m, 0, _N | _S)
        add(m, w - 1, _N | _S)
        put(m, 1, lbl)
        add(b, 0, _N | _E)
        for c in range(1, w - 1):
            add(b, c, _E | _W)
        add(b, w - 1, _N | _W)

    # Sequential edges: a labelled down-arrow on the spine.
    spine_c = 2
    for e in seq_edges:
        i = order[e.src]
        add(bot(i), spine_c, _S)
        add(bot(i) + 1, spine_c, _N | _S)
        put(bot(i) + 2, spine_c, "▼")
        lbl = _port_label(e)
        if lbl:
            put(bot(i) + 1, spine_c + 2, lbl)

    # Lane-routed edges: source right-stub -> vertical lane -> destination ◄.
    for idx, e in enumerate(lane_edges):
        col = lane_x(lane_col[idx])
        si, di = order[e.src], order[e.dst]
        sr, tr = mid(si), mid(di)
        down = di > si
        for c in range(box_w[si], col):
            add(sr, c, _E | _W)
        add(sr, col, _W | _S if down else _W | _N)
        for r in range(min(sr, tr) + 1, max(sr, tr)):
            add(r, col, _N | _S)
        add(tr, col, _W | _N if down else _W | _S)
        for c in range(box_w[di], col):
            add(tr, c, _E | _W)
        put(tr, box_w[di], "◄")
        # Leader east from the corner to the aligned label column; the corner
        # gains an east bit (┘→┴ / ┐→┬) and any lane it crosses becomes ┼.
        add(tr, col, _E)
        for c in range(col + 1, label_col):
            add(tr, c, _E | _W)

    for label_row, label_text in row_label.items():
        put(label_row, label_col, label_text)

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
