"""flow.loader — load and validate WorkflowDef from JSON.

Data flows through explicit ports and edge mappings (see :mod:`flow.models`).
A node declares ``inputs`` / ``outputs`` port lists; an edge declares ``map`` —
``{source_output_port: destination_input_port}``.  The workflow's ``state`` block
seeds the output ports of the reserved source node :data:`flow.models.IN_NODE_ID`
(``$in``), which is referenced by edges like any other source but never appears
in ``wf.nodes``.  Edges may target the reserved sink :data:`flow.models.OUT_NODE_ID`
(``$output``) to collect workflow outputs; ``$output`` is a dst-only mirror of
``$in`` and likewise never appears in ``wf.nodes``.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any

from flow.errors import WorkflowValidationError
from flow.models import IN_NODE_ID, OUT_NODE_ID, Condition, EdgeDef, NodeDef, Port, WorkflowDef

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


def _parse_ports(raw: Any) -> tuple[Port, ...]:
    """Parse a ``inputs``/``outputs`` list of bare names or ``{name,type,optional}`` objects."""
    if not raw:
        return ()
    ports: list[Port] = []
    for item in raw:
        if isinstance(item, dict):
            ports.append(
                Port(
                    name=str(item["name"]),
                    type=str(item.get("type", "string")),
                    optional=bool(item.get("optional", False)),
                )
            )
        else:
            ports.append(Port(name=str(item)))
    return tuple(ports)


def _parse_output_ports(data: dict[str, Any]) -> tuple[Port, ...]:
    """Output ports from ``outputs`` (list) or the ``output`` singular sugar."""
    if "outputs" in data:
        return _parse_ports(data["outputs"])
    raw = data.get("output")
    if raw is None:
        return ()
    if isinstance(raw, dict):
        return (Port(name=str(raw["name"]), type=str(raw.get("type", "string"))),)
    return (Port(name=str(raw)),)


def _parse_node(data: dict[str, Any]) -> NodeDef:
    raw_tools = data.get("tools", [])
    tools: tuple[str, ...] = tuple(str(t) for t in raw_tools) if raw_tools else ()
    raw_output_schema = data.get("output_schema", {})
    output_schema: tuple[tuple[str, str], ...]
    if isinstance(raw_output_schema, dict) and raw_output_schema:
        output_schema = tuple((str(k), str(v)) for k, v in raw_output_schema.items())
    else:
        output_schema = ()
    return NodeDef(
        id=str(data["id"]),
        type=data.get("type", "agent"),
        model=data.get("model"),
        system_prompt=data.get("system_prompt", ""),
        prompt=data.get("prompt", ""),
        tools=tools,
        run=data.get("run"),
        input_ports=_parse_ports(data.get("inputs", [])),
        output_ports=_parse_output_ports(data),
        output_schema=output_schema,
        code=data.get("code"),
        web_search=bool(data.get("web_search", False)),
        web_search_model=data.get("web_search_model"),
    )


def _parse_edge(data: dict[str, Any]) -> EdgeDef:
    when: Condition | None = None
    if "when" in data:
        when = _parse_condition(data["when"])
    loop_max: int | None = None
    if "loop" in data and isinstance(data["loop"], dict):
        loop_max = int(data["loop"]["max"])
    raw_map = data.get("map", {})
    mapping: tuple[tuple[str, str], ...]
    if isinstance(raw_map, dict) and raw_map:
        mapping = tuple((str(s), str(d)) for s, d in raw_map.items())
    else:
        mapping = ()
    return EdgeDef(
        src=str(data["from"]),
        dst=str(data["to"]),
        mapping=mapping,
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

    raw_tools = data.get("tools", {})
    tool_refs: tuple[tuple[str, str], ...]
    if isinstance(raw_tools, dict) and raw_tools:
        tool_refs = tuple((str(k), str(v)) for k, v in raw_tools.items())
    else:
        tool_refs = ()

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
        tool_refs=tool_refs,
    )


def _validate_script_node(node: NodeDef, run_re: re.Pattern[str]) -> None:
    """Validate a script node: exactly one code source; inline code compiles and
    has a ``ctx``-first signature whose remaining params match the declared input ports."""
    has_code = node.code is not None
    has_run = bool(node.run)
    if has_code and has_run:
        raise WorkflowValidationError(f"Script node {node.id!r}: set either 'code' or 'run', not both")
    if not has_code and not has_run:
        raise WorkflowValidationError(f"Script node {node.id!r}: must set 'code' or 'run'")

    if has_run:
        assert node.run is not None
        if not run_re.match(node.run):
            raise WorkflowValidationError(
                f"Script node {node.id!r}: 'run' must match 'module.path:callable', got {node.run!r}"
            )
        return

    # Inline code: must compile, and its function must be (ctx, *input_ports).
    assert node.code is not None
    try:
        tree = ast.parse(node.code, filename=f"<{node.id}>", mode="exec")
    except SyntaxError as exc:
        raise WorkflowValidationError(f"Script node {node.id!r}: invalid code — {exc}") from exc
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    if len(funcs) != 1:
        raise WorkflowValidationError(
            f"Script node {node.id!r}: 'code' must define exactly one top-level function, found {len(funcs)}"
        )
    fn = funcs[0]
    arg_names = [a.arg for a in fn.args.args]
    if not arg_names or arg_names[0] != "ctx":
        raise WorkflowValidationError(f"Script node {node.id!r}: function's first parameter must be 'ctx'")
    declared = set(node.input_names)
    got = set(arg_names[1:])
    if got != declared:
        raise WorkflowValidationError(
            f"Script node {node.id!r}: function params {sorted(got)} != declared inputs {sorted(declared)}"
        )


def _output_port_names(wf: WorkflowDef, node_id: str, in_ports: set[str]) -> set[str]:
    """Output port names available on *node_id* (the ``$in`` source exposes state keys)."""
    if node_id == IN_NODE_ID:
        return in_ports
    for n in wf.nodes:
        if n.id == node_id:
            return set(n.output_names)
    return set()


def validate_workflow(wf: WorkflowDef) -> None:
    """Validate a WorkflowDef. Raises WorkflowValidationError on any problem."""
    node_ids = [n.id for n in wf.nodes]

    for nid in node_ids:
        if not nid:
            raise WorkflowValidationError("Node id must be non-empty")
        if nid == IN_NODE_ID:
            raise WorkflowValidationError(f"Node id {IN_NODE_ID!r} is reserved for the workflow input source")
        if nid == OUT_NODE_ID:
            raise WorkflowValidationError(f"Node id {OUT_NODE_ID!r} is reserved for the workflow output sink")
    if len(node_ids) != len(set(node_ids)):
        raise WorkflowValidationError(f"Duplicate node ids: {node_ids}")

    # $in is index -1 (earliest); real nodes are 0..n-1 in declaration order;
    # $output is index n (latest) so edges into it are always forward.
    node_index: dict[str, int] = {nid: i for i, nid in enumerate(node_ids)}
    node_index[IN_NODE_ID] = -1
    node_index[OUT_NODE_ID] = len(node_ids)
    node_by_id = {n.id: n for n in wf.nodes}
    in_ports = {k for k, _ in wf.initial_state}

    if wf.entry not in node_by_id:
        raise WorkflowValidationError(f"Entry node {wf.entry!r} not found in nodes")

    _run_re = re.compile(r"^[\w.]+:[\w]+$")

    # Validate the custom-tool manifest and build the set of resolvable tool
    # names (built-ins + manifest keys) so unknown node tool names fail fast.
    from flow.tools import default_registry

    manifest_names: set[str] = set()
    for tname, ref in wf.tool_refs:
        if not tname:
            raise WorkflowValidationError("Tool manifest: tool name must be non-empty")
        if not _run_re.match(ref):
            raise WorkflowValidationError(
                f"Tool {tname!r}: ref must match 'module.path:callable', got {ref!r}"
            )
        manifest_names.add(tname)
    known_tools = default_registry().names() | manifest_names

    for node in wf.nodes:
        for tool in node.tools:
            if not tool:
                raise WorkflowValidationError(f"Node {node.id!r}: tool name must be non-empty")
            if tool not in known_tools:
                known = ", ".join(sorted(known_tools)) or "<none>"
                raise WorkflowValidationError(
                    f"Node {node.id!r} references unknown tool {tool!r}. Known tools: {known}"
                )
        if node.type == "script":
            _validate_script_node(node, _run_re)
        elif node.type == "agent":
            if node.run is not None:
                raise WorkflowValidationError(f"Agent node {node.id!r} must not set 'run'")
            if node.code is not None:
                raise WorkflowValidationError(f"Agent node {node.id!r} must not set 'code'")

    # Edges: endpoints exist, ports exist, mappings are well-formed, loops bounded.
    # fed[(dst_node, dst_port)] counts feeding data edges — every input port needs
    # one; unconditional_fed counts only always-on (non-when, non-loop) feeders, so
    # two producers can't silently target the same port (the old shared-key clash).
    fed: dict[tuple[str, str], int] = {}
    unconditional_fed: dict[tuple[str, str], int] = {}
    for edge in wf.edges:
        if edge.src == OUT_NODE_ID:
            raise WorkflowValidationError(f"Edge src {OUT_NODE_ID!r} is not allowed ($output is a sink only)")
        if edge.src != IN_NODE_ID and edge.src not in node_by_id:
            raise WorkflowValidationError(f"Edge src {edge.src!r} not found in nodes")
        if edge.dst != OUT_NODE_ID and edge.dst not in node_by_id:
            raise WorkflowValidationError(f"Edge dst {edge.dst!r} not found in nodes")
        if edge.dst == IN_NODE_ID:
            raise WorkflowValidationError(f"Edge dst {IN_NODE_ID!r} is not allowed ($in is a source only)")

        src_outputs = _output_port_names(wf, edge.src, in_ports)
        # $output is a free-form sink: its destination "ports" are arbitrary output
        # keys, so only the source port must exist (no declared input ports to check,
        # and it never needs to be "fed").
        if edge.dst == OUT_NODE_ID:
            for sport, _dport in edge.mapping:
                if sport not in src_outputs:
                    raise WorkflowValidationError(
                        f"Edge {edge.src!r}->{edge.dst!r}: source has no output port {sport!r}"
                    )
            continue

        dst_inputs = set(node_by_id[edge.dst].input_names)
        for sport, dport in edge.mapping:
            if sport not in src_outputs:
                raise WorkflowValidationError(
                    f"Edge {edge.src!r}->{edge.dst!r}: source has no output port {sport!r}"
                )
            if dport not in dst_inputs:
                raise WorkflowValidationError(
                    f"Edge {edge.src!r}->{edge.dst!r}: destination has no input port {dport!r}"
                )
            fed[(edge.dst, dport)] = fed.get((edge.dst, dport), 0) + 1
            if edge.when is None and edge.loop_max is None:
                unconditional_fed[(edge.dst, dport)] = unconditional_fed.get((edge.dst, dport), 0) + 1

        # back-edge (dst not strictly after src) must be a bounded loop
        if node_index[edge.dst] <= node_index[edge.src]:
            if not (edge.loop_max is not None and edge.loop_max >= 1):
                raise WorkflowValidationError(f"Back-edge {edge.src!r} -> {edge.dst!r} must have loop.max >= 1")

    # Two unconditional producers into one input port is the old shared-key clash.
    for (dst, port), count in unconditional_fed.items():
        if count > 1:
            raise WorkflowValidationError(
                f"Node {dst!r}: input port {port!r} is fed by {count} unconditional edges "
                f"(ambiguous producer; use conditional edges if mutually exclusive)"
            )

    # Every declared input port must be fed by at least one edge mapping — unless
    # it is marked optional (e.g. a loop-carried value absent on the first pass).
    for node in wf.nodes:
        for p in node.input_ports:
            if p.optional:
                continue
            if fed.get((node.id, p.name), 0) == 0:
                raise WorkflowValidationError(
                    f"Node {node.id!r}: input port {p.name!r} is not fed by any edge mapping"
                )


def load_workflow(path: str | Path) -> WorkflowDef:
    """Load a WorkflowDef from a JSON file at *path*."""
    p = Path(path)
    logger.debug("Loading workflow from %s", p)
    with p.open() as fh:
        data: dict[str, Any] = json.load(fh)
    wf = parse_workflow(data)
    validate_workflow(wf)
    return wf
