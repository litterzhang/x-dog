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
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from flow.coerce import VALID_TYPES
from flow.errors import FlowWarning, WorkflowValidationError
from flow.models import (
    IN_NODE_ID,
    OUT_NODE_ID,
    Condition,
    EdgeDef,
    NodeDef,
    Port,
    RetryPolicy,
    WorkflowDef,
    entry_frontier,
)

logger = logging.getLogger(__name__)

# CLI agent backends (docs/cli-agent.md): an agent node with one of these shells
# out to that CLI instead of the in-process SDK.
_KNOWN_CLI_BACKENDS = frozenset({"claude-cli", "codex-cli"})


def _parse_condition(data: Any) -> Condition:
    if not isinstance(data, dict):
        raise WorkflowValidationError(f"Condition must be a dict, got {type(data)}")
    if "equals" in data:
        inner = data["equals"]
        return Condition(op="equals", value=str(inner["value"]), text=str(inner["text"]))
    if "contains" in data:
        inner = data["contains"]
        return Condition(op="contains", value=str(inner["value"]), text=str(inner["text"]))
    for _num_op in ("gt", "gte", "lt", "lte"):
        if _num_op in data:
            inner = data[_num_op]
            return Condition(op=_num_op, value=str(inner["value"]), text=str(inner["text"]))
    if "not" in data:
        return Condition(op="not", children=(_parse_condition(data["not"]),))
    if "and" in data:
        return Condition(op="and", children=tuple(_parse_condition(c) for c in data["and"]))
    if "or" in data:
        return Condition(op="or", children=tuple(_parse_condition(c) for c in data["or"]))
    raise WorkflowValidationError(f"Unknown condition keys: {list(data.keys())}")


def _parse_ports(raw: Any) -> tuple[Port, ...]:
    """Parse an ``inputs``/``outputs`` list.

    Each entry is a bare name (a required ``string`` port) or an object.  The
    object form accepts the legacy ``{name, type, optional}`` and the new
    ``{name, schema, required}`` (a nested JSON Schema).  ``optional: true`` maps
    to ``required: false``.
    """
    if not raw:
        return ()
    ports: list[Port] = []
    for item in raw:
        if isinstance(item, dict):
            ports.append(_port_from_obj(item))
        else:
            ports.append(Port(name=str(item)))
    return tuple(ports)


def _port_from_obj(item: dict[str, Any]) -> Port:
    """Build a Port from an object entry (legacy type/optional or new schema/required)."""
    name = str(item["name"])
    schema = item.get("schema")
    if isinstance(schema, dict):
        required = bool(item.get("required", not item.get("optional", False)))
        return Port(name=name, schema=schema, required=required)
    # Legacy scalar form: {name, type?, optional?}
    return Port(
        name=name,
        type=str(item.get("type", "string")),
        optional=bool(item.get("optional", False)),
    )


def _parse_output_ports(data: dict[str, Any]) -> tuple[Port, ...]:
    """Output ports from ``outputs`` (list) or the ``output`` singular sugar."""
    if "outputs" in data:
        return _parse_ports(data["outputs"])
    raw = data.get("output")
    if raw is None:
        return ()
    if isinstance(raw, dict):
        return (_port_from_obj(raw),)
    return (Port(name=str(raw)),)


def _parse_node(
    data: dict[str, Any],
    *,
    base_dir: Path | None = None,
    _seen: frozenset[Path] | None = None,
) -> NodeDef:
    raw_tools = data.get("tools", [])
    tools: tuple[str, ...] = tuple(str(t) for t in raw_tools) if raw_tools else ()
    node_id = str(data["id"])
    retry: RetryPolicy | None = None
    raw_retry = data.get("retry")
    if raw_retry is not None:
        if not isinstance(raw_retry, dict):
            raise WorkflowValidationError(f"Node {node_id!r}: retry must be an object")
        raw_max = raw_retry.get("max", 0)
        if not isinstance(raw_max, int) or raw_max < 0:
            raise WorkflowValidationError(f"Node {node_id!r}: retry.max must be >= 0")
        raw_backoff = raw_retry.get("backoff", 0.0)
        if not isinstance(raw_backoff, (int, float)) or raw_backoff < 0:
            raise WorkflowValidationError(f"Node {node_id!r}: retry.backoff must be >= 0")
        retry = RetryPolicy(max=int(raw_max), backoff=float(raw_backoff))
    raw_on_error = data.get("on_error", "fail")
    if raw_on_error not in ("fail", "isolate"):
        raise WorkflowValidationError(f"Node {node_id!r}: on_error must be 'fail' or 'isolate'")
    on_error: Literal["fail", "isolate"] = raw_on_error
    raw_type = data.get("type", "agent")
    if raw_type not in ("agent", "script", "human", "subflow"):
        raise WorkflowValidationError(
            f"Node {node_id!r}: type must be 'agent', 'script', 'human', or 'subflow', got {raw_type!r}"
        )
    node_type: Literal["agent", "script", "human", "subflow"] = raw_type
    signal = str(data.get("signal", ""))

    # Sub-workflow (G5): parse the INLINE child and DERIVE the node's ports from
    # its signature — the author does not declare inputs/outputs on a subflow node.
    child: WorkflowDef | None = None
    sub_input_ports: tuple[Port, ...] = _parse_ports(data.get("inputs", []))
    sub_output_ports: tuple[Port, ...] = _parse_output_ports(data)
    if node_type == "subflow":
        raw_child = data.get("subflow")
        if data.get("inputs") or data.get("outputs") or data.get("output"):
            raise WorkflowValidationError(
                f"Subflow node {node_id!r}: must not declare 'inputs'/'outputs' — they are "
                f"derived from the child workflow's signature"
            )
        if isinstance(raw_child, str):
            # Path reference: resolve against base_dir, guard against cycles, and
            # inline the loaded child (execution/codegen/serialize see an inline child).
            if base_dir is None:
                raise WorkflowValidationError(
                    f"Subflow node {node_id!r}: a path reference {raw_child!r} needs a base directory; "
                    f"load via load_workflow(path), not parse_workflow(dict)"
                )
            child_path = (base_dir / raw_child).resolve()
            seen = _seen or frozenset()
            if child_path in seen:
                raise WorkflowValidationError(
                    f"Subflow node {node_id!r}: cyclic subflow reference to {child_path}"
                )
            if not child_path.is_file():
                raise WorkflowValidationError(
                    f"Subflow node {node_id!r}: child workflow file not found: {child_path}"
                )
            with child_path.open(encoding="utf-8") as _fh:
                child_data = json.load(_fh)
            child = parse_workflow(child_data, base_dir=child_path.parent, _seen=seen | {child_path})
        elif isinstance(raw_child, dict):
            child = parse_workflow(raw_child, base_dir=base_dir, _seen=_seen)
        else:
            raise WorkflowValidationError(
                f"Subflow node {node_id!r}: must set 'subflow' to an inline child workflow object "
                f"or a path string to a child workflow JSON"
            )
        in_sig = workflow_input_schema(child)["properties"]
        out_sig = workflow_output_schema(child)["properties"]
        assert isinstance(in_sig, dict) and isinstance(out_sig, dict)
        sub_input_ports = tuple(
            Port(name=k, schema=v if isinstance(v, dict) else {"type": "string"}) for k, v in in_sig.items()
        )
        sub_output_ports = tuple(
            Port(name=k, schema=v if isinstance(v, dict) else {"type": "string"}) for k, v in out_sig.items()
        )

    # CLI agent backend fields (docs/cli-agent.md).
    backend = data.get("backend")
    backend_val: str | None = str(backend) if backend is not None else None
    raw_allowed = data.get("allowed_tools", [])
    allowed_tools: tuple[str, ...] = tuple(str(t) for t in raw_allowed) if raw_allowed else ()
    raw_mcp = data.get("mcp_servers", {})
    mcp_servers: tuple[tuple[str, dict[str, object]], ...]
    if isinstance(raw_mcp, dict) and raw_mcp:
        mcp_list: list[tuple[str, dict[str, object]]] = []
        for k, v in raw_mcp.items():
            if not isinstance(v, dict):
                raise WorkflowValidationError(f"Node {node_id!r}: mcp_servers[{k!r}] must be an object")
            mcp_list.append((str(k), v))
        mcp_servers = tuple(mcp_list)
    else:
        mcp_servers = ()

    return NodeDef(
        id=node_id,
        type=node_type,
        signal=signal,
        model=data.get("model"),
        system_prompt=data.get("system_prompt", ""),
        prompt=data.get("prompt", ""),
        tools=tools,
        run=data.get("run"),
        input_ports=sub_input_ports,
        output_ports=sub_output_ports,
        code=data.get("code"),
        web_search=bool(data.get("web_search", False)),
        web_search_model=data.get("web_search_model"),
        retry=retry,
        on_error=on_error,
        deterministic=bool(data.get("deterministic", False)),
        child=child,
        backend=backend_val,
        allowed_tools=allowed_tools,
        mcp_servers=mcp_servers,
    )


def _parse_edge(data: dict[str, Any]) -> EdgeDef:
    when: Condition | None = None
    if "when" in data:
        when = _parse_condition(data["when"])
    loop_max: int | None = None
    if "loop" in data and isinstance(data["loop"], dict):
        if "max" not in data["loop"]:
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: loop must declare an integer 'max'"
            )
        try:
            loop_max = int(data["loop"]["max"])
        except (TypeError, ValueError):
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: loop 'max' must be an integer,"
                f" got {data['loop']['max']!r}"
            ) from None
        if loop_max <= 0:
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: loop 'max' must be positive,"
                f" got {loop_max}"
            )
    raw_map = data.get("map", {})
    mapping: tuple[tuple[str, str], ...]
    if isinstance(raw_map, dict) and raw_map:
        mapping = tuple((str(s), str(d)) for s, d in raw_map.items())
    else:
        mapping = ()
    fan_out: str | None = None
    raw_fan_out = data.get("fan_out")
    if raw_fan_out is not None:
        fan_out = str(raw_fan_out)
    fan_in: Literal["list"] | None = None
    raw_fan_in = data.get("fan_in")
    if raw_fan_in is not None:
        if raw_fan_in != "list":
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: fan_in must be 'list',"
                f" got {raw_fan_in!r}"
            )
        fan_in = "list"
    return EdgeDef(
        src=str(data["from"]),
        dst=str(data["to"]),
        mapping=mapping,
        when=when,
        loop_max=loop_max,
        fan_out=fan_out,
        fan_in=fan_in,
    )


def parse_workflow(
    data: dict[str, Any],
    *,
    base_dir: Path | None = None,
    _seen: frozenset[Path] | None = None,
) -> WorkflowDef:
    """Build a WorkflowDef from a raw dict (already parsed from JSON).

    ``base_dir`` is the directory a ``"subflow": "./child.json"`` path reference is
    resolved against (set by :func:`load_workflow`).  ``_seen`` carries the set of
    already-loaded child paths so a cyclic subflow reference (A -> B -> A) is caught
    at load time instead of recursing forever.
    """
    name = str(data.get("name", ""))
    provider = str(data.get("provider", ""))
    entry = str(data.get("entry", ""))
    default_model = str(data.get("defaults", {}).get("model", ""))

    raw_state = data.get("state", {})
    initial_state: tuple[tuple[str, object], ...]
    if isinstance(raw_state, dict):
        # Keep seed values type-native (a structured seed stays a dict/list, not a
        # Python repr); the wire format carries them through unchanged.
        initial_state = tuple((str(k), v) for k, v in raw_state.items())
    else:
        initial_state = ()

    # Optional per-$in JSON Schema (opt-in typed input signature).  A ``state`` key
    # without a schema here stays untyped; a key WITH one is type-checked.
    raw_in_schema = data.get("in_schema", {})
    in_schema: tuple[tuple[str, dict[str, object]], ...]
    if isinstance(raw_in_schema, dict) and raw_in_schema:
        parsed: list[tuple[str, dict[str, object]]] = []
        for k, v in raw_in_schema.items():
            if not isinstance(v, dict):
                raise WorkflowValidationError(f"in_schema[{k!r}] must be a JSON Schema object")
            parsed.append((str(k), v))
        in_schema = tuple(parsed)
    else:
        in_schema = ()

    raw_tools = data.get("tools", {})
    tool_refs: tuple[tuple[str, str], ...]
    if isinstance(raw_tools, dict) and raw_tools:
        tool_refs = tuple((str(k), str(v)) for k, v in raw_tools.items())
    else:
        tool_refs = ()

    raw_max_concurrency = data.get("max_concurrency", 0)
    if not isinstance(raw_max_concurrency, int) or raw_max_concurrency < 0:
        raise WorkflowValidationError("max_concurrency must be an int >= 0")
    max_concurrency = raw_max_concurrency

    raw_fan_max = data.get("fan_max_concurrency", 0)
    if not isinstance(raw_fan_max, int) or raw_fan_max < 0:
        raise WorkflowValidationError("fan_max_concurrency must be an int >= 0")
    fan_max_concurrency = raw_fan_max

    nodes = tuple(_parse_node(n, base_dir=base_dir, _seen=_seen) for n in data.get("nodes", []))
    edges = tuple(_parse_edge(e) for e in data.get("edges", []))

    return WorkflowDef(
        name=name,
        provider=provider,
        entry=entry,
        nodes=nodes,
        edges=edges,
        default_model=default_model,
        initial_state=initial_state,
        in_schema=in_schema,
        tool_refs=tool_refs,
        max_concurrency=max_concurrency,
        fan_max_concurrency=fan_max_concurrency,
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


def _jsonpath_root(key: str) -> tuple[str, bool]:
    """Return (root_field, has_subpath) for an edge-mapping JSONPath source key.

    ``$.verdict.within_budget`` -> ("verdict", True); ``$.plan`` -> ("plan", False);
    a bare ``plan`` -> ("plan", False); ``$.tasks[0]`` -> ("tasks", True).
    """
    body = key[2:] if key.startswith("$.") else (key[1:] if key.startswith("$") else key)
    # The root field ends at the first '.' or '[' separator.
    root = re.split(r"[.\[]", body, maxsplit=1)[0]
    has_sub = len(root) < len(body)
    return root, has_sub


# A ``{{ <jsonpath> }}`` placeholder — mirrors flow.interpolate._PATTERN.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def _placeholder_roots(text: str) -> list[str]:
    """Root port names referenced by the ``{{ $.path }}`` placeholders in *text*.

    Skips a placeholder whose root can't be determined (an odd JSONPath),
    so strict validation only flags a clearly-wrong root.
    """
    roots: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(text):
        root, _ = _jsonpath_root(m.group(1))
        if root:
            roots.append(root)
    return roots


def _condition_operand_roots(cond: Condition) -> list[str]:
    """All placeholder roots referenced by a condition tree's value/text operands."""
    roots: list[str] = []
    if cond.value is not None:
        roots += _placeholder_roots(cond.value)
    if cond.text is not None:
        roots += _placeholder_roots(cond.text)
    for child in cond.children:
        roots += _condition_operand_roots(child)
    return roots


# A JSONPath tail step: a ``.field`` name, or an ``[index]``/``[*]`` bracket.
_JSONPATH_STEP = re.compile(r"\.(\w+)|\[([^\]]*)\]")


def _jsonpath_tail_steps(key: str) -> list[str] | None:
    """Parse the tail of a mapping JSONPath into ordered descent steps.

    Returns a list where a field step is its name and a plain-index step is the
    marker ``"[]"``.  Returns None when the tail contains a construct we can't
    statically type (a wildcard ``[*]``, a filter ``[?(...)]``, recursive ``..``,
    a union) — the caller then leaves the path un-typed (lenient).
    """
    body = key[2:] if key.startswith("$.") else (key[1:] if key.startswith("$") else key)
    # Everything after the root token (the name up to the first '.' or '[').
    root_len = len(re.split(r"[.\[]", body, maxsplit=1)[0])
    tail_src = body[root_len:]
    if not tail_src:
        return []
    if ".." in tail_src:
        return None  # recursive descent — not statically typable
    steps: list[str] = []
    pos = 0
    for m in _JSONPATH_STEP.finditer(tail_src):
        if m.start() != pos:  # a gap means an unparsed construct
            return None
        pos = m.end()
        field, bracket = m.group(1), m.group(2)
        if field is not None:
            steps.append(field)
        elif bracket is not None:
            # Only a plain integer index descends to the item type.  A wildcard
            # ``[*]`` (all elements), a filter, or a quoted key is not statically
            # typable -> leave the whole path un-typed.
            b = bracket.strip()
            if b.lstrip("-").isdigit():
                steps.append("[]")
            else:
                return None
    if pos != len(tail_src):
        return None
    return steps


def _schema_subtype(schema: dict[str, object], steps: list[str]) -> str | None:
    """Descend *schema* along *steps*; return the leaf ``type`` name, or None.

    A ``field`` step reads ``schema["properties"][field]``; an ``"[]"`` step reads
    ``schema["items"]``.  Any step the schema doesn't describe (no ``properties``/
    ``items``, missing field) yields None — the path is left un-typed (lenient).
    """
    cur: object = schema
    for step in steps:
        if not isinstance(cur, dict):
            return None
        if step == "[]":
            cur = cur.get("items")
        else:
            props = cur.get("properties")
            if not isinstance(props, dict):
                return None
            cur = props.get(step)
        if cur is None:
            return None
    if not isinstance(cur, dict):
        return None
    t = cur.get("type")
    return t if isinstance(t, str) else None


def _output_port_schemas(wf: WorkflowDef, node_id: str) -> dict[str, dict[str, object]]:
    """Map a node's output port name -> its full JSON Schema.

    ``$in`` seed keys are untyped UNLESS the workflow declares ``in_schema`` for
    them (opt-in): a declared key returns its schema (so sub-field / fan_out type
    checks apply); an undeclared key is absent (exempt), as before.
    """
    if node_id == IN_NODE_ID:
        return {k: v for k, v in wf.in_schema}
    for n in wf.nodes:
        if n.id == node_id:
            return {p.name: p.schema for p in n.output_ports}
    return {}


def _output_port_types(wf: WorkflowDef, node_id: str) -> dict[str, str]:
    """Map a node's output port name -> declared type.

    ``$in`` seed keys are untyped UNLESS declared in ``in_schema`` (opt-in): a
    declared key returns its top-level ``type``; an undeclared key is absent
    (edges out of it are exempt from type checking), as before.
    """
    if node_id == IN_NODE_ID:
        out: dict[str, str] = {}
        for k, schema in wf.in_schema:
            t = schema.get("type")
            if isinstance(t, str):
                out[k] = t
        return out
    for n in wf.nodes:
        if n.id == node_id:
            return {p.name: p.type for p in n.output_ports}
    return {}


def infer_input_schema(wf: WorkflowDef) -> dict[str, dict[str, object]]:
    """Infer a JSON Schema per ``$in`` seed key from its typed consumers.

    For each key, collect the schema of every input port fed by a plain mapped
    edge out of ``$in`` (the CONSUMER's declared type — not the seed's runtime
    value type, which is unreliable: ``"347"`` legitimately feeds an ``integer``
    port under flow's tolerant coercion).  A key is typed only when all its
    consumers agree; a key with conflicting consumers or no typed consumer is
    omitted (left untyped).

    ``fan_out`` edges never reach here: fan-out from ``$in`` already REQUIRES an
    explicit ``in_schema`` (see ``_validate_fan_out_edge``), so a workflow that
    fans out of ``$in`` has an explicit signature and skips inference entirely.
    """
    node_by_id = {n.id: n for n in wf.nodes}
    # key -> set of frozen (json) schemas seen from consumers; >1 distinct = conflict.
    seen: dict[str, list[dict[str, object]]] = {}
    for edge in wf.edges:
        if edge.src != IN_NODE_ID or edge.fan_out is not None:
            continue
        dst = node_by_id.get(edge.dst)
        if dst is None:
            continue
        dst_ports = {p.name: p.schema for p in dst.input_ports}
        for sport, dport in edge.mapping:
            head, subpath = _jsonpath_root(sport)
            if subpath or dport not in dst_ports:
                continue  # sub-field seeds / control-only ports are not typed here
            seen.setdefault(head, []).append(dst_ports[dport])
    inferred: dict[str, dict[str, object]] = {}
    for key, schemas in seen.items():
        first = schemas[0]
        if all(s == first for s in schemas):  # all consumers agree
            inferred[key] = dict(first)
        # else: conflicting consumers -> leave untyped (author must declare)
    return inferred


def workflow_input_schema(wf: WorkflowDef) -> dict[str, object]:
    """The workflow's typed input signature as a JSON Schema object.

    If the workflow declares ``in_schema`` explicitly, that IS the signature (no
    inference, no merge — the author is authoritative).  Otherwise the signature
    is inferred from each ``$in`` key's typed consumers (:func:`infer_input_schema`).
    Undeclared/uninferable keys are simply absent from the properties.
    """
    props = {k: v for k, v in wf.in_schema} if wf.in_schema else infer_input_schema(wf)
    return {"type": "object", "properties": props, "required": sorted(props)}


def workflow_output_schema(wf: WorkflowDef) -> dict[str, object]:
    """The workflow's output signature as a JSON Schema object, derived statically.

    For every edge into the ``$output`` sink, the mapped output key takes the
    schema of its source node's output port (looked up by port name).  The result
    is ``{"type": "object", "properties": {out_key: port_schema, ...}}``.  A key
    whose source port has no schema, or comes from an untyped ``$in`` seed, is
    typed as a bare ``string`` (the wire default).  A ``when``-guarded ``$output``
    edge still contributes its key (the schema is static; the guard is runtime).
    """
    props: dict[str, object] = {}
    for edge in wf.edges:
        if edge.dst != OUT_NODE_ID:
            continue
        src_schemas = _output_port_schemas(wf, edge.src)
        for sport, okey in edge.mapping:
            # $output mappings map whole ports (sub-field keys are rejected in
            # validation), so the source key is a plain port name.
            schema = src_schemas.get(sport)
            props[okey] = dict(schema) if isinstance(schema, dict) else {"type": "string"}
    return {"type": "object", "properties": props, "required": sorted(props)}


def _detect_cycle(wf: WorkflowDef) -> list[str] | None:
    """Return a cycle path over non-loop edges, or None if the graph is acyclic.

    A legal bounded loop declares ``loop.max`` on its back-edge, so those edges
    are excluded — only unbounded cycles (which would never terminate) are
    reported.  ``$in``/``$output`` are excluded (a source and a sink).  Uses an
    iterative white/grey/black DFS so a large graph can't blow the stack.
    """
    adj: dict[str, list[str]] = defaultdict(list)
    for e in wf.edges:
        if e.loop_max is not None:
            continue  # bounded loop back-edge is legal
        if e.src in (IN_NODE_ID, OUT_NODE_ID) or e.dst in (IN_NODE_ID, OUT_NODE_ID):
            continue
        adj[e.src].append(e.dst)

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {n.id: WHITE for n in wf.nodes}
    # (node, index-into-neighbours, path-so-far). path lets us report the cycle.
    for start in (n.id for n in wf.nodes):
        if color[start] != WHITE:
            continue
        stack: list[tuple[str, int, list[str]]] = [(start, 0, [start])]
        color[start] = GREY
        while stack:
            node, idx, path = stack[-1]
            neighbours = adj.get(node, ())
            if idx < len(neighbours):
                stack[-1] = (node, idx + 1, path)
                nxt = neighbours[idx]
                if color.get(nxt, BLACK) == GREY:
                    # Found a back-edge into the current DFS stack — a cycle.
                    return [*path[path.index(nxt):], nxt]
                if color.get(nxt, BLACK) == WHITE:
                    color[nxt] = GREY
                    stack.append((nxt, 0, [*path, nxt]))
            else:
                color[node] = BLACK
                stack.pop()
    return None


def _validate_fan_out_edge(
    wf: WorkflowDef,
    edge: EdgeDef,
    src_outputs: set[str],
    loop_touched: set[str],
) -> None:
    """Validate a ``fan_out`` edge (G1): the named source port must be an ARRAY
    output; the worker must not be a loop node or itself a fan-out worker (no
    nesting); the array must be one of the edge's mapping sources."""
    assert edge.fan_out is not None
    label = f"Edge {edge.src!r}->{edge.dst!r}"
    if edge.fan_out not in src_outputs:
        raise WorkflowValidationError(
            f"{label}: fan_out names {edge.fan_out!r} which is not an output port of {edge.src!r}"
        )
    # The fan_out port must be declared as an array.  A real node's output port
    # carries its schema; a ``$in`` seed carries one only when declared in
    # ``in_schema`` (opt-in typed input) — an undeclared ``$in`` key stays untyped
    # and cannot drive a typed fan-out.
    src_schemas = _output_port_schemas(wf, edge.src)
    if edge.src == IN_NODE_ID and edge.fan_out not in src_schemas:
        raise WorkflowValidationError(
            f"{label}: fan_out from {IN_NODE_ID} requires an array in_schema for {edge.fan_out!r} "
            f"(untyped seed cannot drive fan-out)"
        )
    src_schema = src_schemas.get(edge.fan_out, {})
    if src_schema.get("type") != "array":
        raise WorkflowValidationError(
            f"{label}: fan_out port {edge.fan_out!r} must be an array output, "
            f"got type {src_schema.get('type', 'string')!r}"
        )
    # The fan_out port must appear as a mapping source (its elements feed the worker).
    if edge.fan_out not in {_jsonpath_root(s)[0] for s, _ in edge.mapping}:
        raise WorkflowValidationError(
            f"{label}: fan_out port {edge.fan_out!r} must be mapped to a worker input port"
        )
    # v1 scope guards: no worker inside a loop, no fan_out on a loop back-edge, no nesting.
    if edge.loop_max is not None:
        raise WorkflowValidationError(f"{label}: a fan_out edge may not be a bounded loop back-edge")
    if edge.dst in loop_touched:
        raise WorkflowValidationError(
            f"{label}: fan-out worker {edge.dst!r} may not also be part of a bounded loop (v1)"
        )
    if any(e.fan_out is not None and e.dst == edge.src for e in wf.edges):
        raise WorkflowValidationError(
            f"{label}: nested fan-out is not supported (v1) — {edge.src!r} is itself a fan-out worker"
        )


def _validate_fan_in_edge(edge: EdgeDef, fan_out_workers: set[str]) -> None:
    """Validate a ``fan_in`` edge (G1): its source must be a fan-out worker."""
    assert edge.fan_in is not None
    if edge.src not in fan_out_workers:
        raise WorkflowValidationError(
            f"Edge {edge.src!r}->{edge.dst!r}: fan_in source {edge.src!r} is not a fan-out worker "
            f"(no incoming fan_out edge feeds it)"
        )


def _validate_subflow_node(node: NodeDef) -> None:
    """Validate a ``type="subflow"`` node (G5): inline child, no nesting, strict
    signature, and the mutually-exclusive-field rules.  Recursively validates the
    child so a broken child fails at the parent's load time."""
    if node.child is None:
        raise WorkflowValidationError(f"Subflow node {node.id!r}: missing inline child workflow")
    if node.code is not None:
        raise WorkflowValidationError(f"Subflow node {node.id!r} must not set 'code'")
    if node.prompt or node.system_prompt:
        raise WorkflowValidationError(f"Subflow node {node.id!r} must not set a prompt")
    if node.tools:
        raise WorkflowValidationError(f"Subflow node {node.id!r} must not set 'tools'")
    # v1: no nested subflows (recursion is structurally impossible with inline
    # children, but a child may still itself contain a subflow node — reject that).
    if any(cn.type == "subflow" for cn in node.child.nodes):
        raise WorkflowValidationError(
            f"Subflow node {node.id!r}: nested sub-workflows are not supported (v1)"
        )
    # Recursively validate the child (a broken child fails here).
    validate_workflow(node.child)
    # Strict signature (option A): every derived input port must be typed.  A key
    # that is neither declared in the child's in_schema nor inferable is dropped by
    # workflow_input_schema, so an unfed parent mapping to it would fail the normal
    # port check; but a fed key with a bare "string" fallback from an untyped
    # consumer is still allowed (string is a concrete type).  Nothing more to do:
    # the derived ports already ARE the strict signature.


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

    # Reject an unbounded cycle first (a loop without a declared loop.max would
    # never terminate).  Bounded loops declare loop.max and are excluded.  Doing
    # this before the entry check gives the more actionable error: a full cycle
    # leaves an empty entry frontier, but "cycle" explains why.
    cycle = _detect_cycle(wf)
    if cycle is not None:
        raise WorkflowValidationError(
            f"Workflow has a cycle with no bounded loop: {' -> '.join(cycle)}. "
            f"Add loop.max to a back-edge to make it a bounded loop."
        )

    # ``entry`` is optional: when set it names a single start node (must exist);
    # when empty the entry frontier is derived from the nodes that depend only on
    # ``$in``.  Either way at least one start node must exist.
    if wf.entry and wf.entry not in node_by_id:
        raise WorkflowValidationError(f"Entry node {wf.entry!r} not found in nodes")
    if not entry_frontier(wf):
        raise WorkflowValidationError(
            "Workflow has no entry node: it has no nodes, or every node has a predecessor so "
            "nothing can start. Feed at least one node from $in (or set an explicit entry)."
        )

    # A provider is required only if the workflow has an SDK agent node (an agent
    # node with no CLI backend).  A pure-CLI (or script-only) workflow needs none —
    # a CLI agent node reuses the CLI's own auth.  See docs/cli-agent.md §7.
    _has_sdk_agent = any(n.type == "agent" and n.backend is None for n in wf.nodes)
    if _has_sdk_agent and not wf.provider:
        raise WorkflowValidationError(
            "Workflow has an SDK agent node but no 'provider'. Set a provider, or give the "
            "agent node a CLI 'backend' (e.g. 'claude-cli') which needs no provider."
        )

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
        # Every declared port type must be one of the known JSON types, so a typo
        # like "int" fails at load time instead of surfacing later in to_python.
        for _port in (*node.input_ports, *node.output_ports):
            if _port.type not in VALID_TYPES:
                raise WorkflowValidationError(
                    f"Node {node.id!r}: port {_port.name!r} has unknown type {_port.type!r}; "
                    f"expected one of {', '.join(VALID_TYPES)}"
                )
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
            if node.backend is not None and node.backend not in _KNOWN_CLI_BACKENDS:
                known = ", ".join(sorted(_KNOWN_CLI_BACKENDS))
                raise WorkflowValidationError(
                    f"Agent node {node.id!r}: unknown backend {node.backend!r}; expected one of {known}"
                )
            if node.backend is None and (node.allowed_tools or node.mcp_servers):
                raise WorkflowValidationError(
                    f"Agent node {node.id!r}: 'allowed_tools'/'mcp_servers' require a CLI 'backend'"
                )
            # Strict interpolation: every {{ $.x }} in a prompt must root at a
            # declared input port (a typo would otherwise silently interpolate to "").
            _in_names = set(node.input_names)
            for _tmpl in (node.system_prompt, node.prompt):
                for _root in _placeholder_roots(_tmpl):
                    if _root not in _in_names:
                        raise WorkflowValidationError(
                            f"Agent node {node.id!r}: prompt references {{{{ ${_root} }}}} but "
                            f"{_root!r} is not a declared input port"
                        )
        elif node.type == "human":
            if not node.signal:
                raise WorkflowValidationError(f"Human node {node.id!r} must declare a non-empty 'signal'")
            if node.code is not None:
                raise WorkflowValidationError(f"Human node {node.id!r} must not set 'code'")
            if node.run is not None:
                raise WorkflowValidationError(f"Human node {node.id!r} must not set 'run'")
            if node.prompt:
                raise WorkflowValidationError(f"Human node {node.id!r} must not set 'prompt'")
            if node.tools:
                raise WorkflowValidationError(f"Human node {node.id!r} must not set 'tools'")
            if len(node.output_ports) > 1:
                raise WorkflowValidationError(f"Human node {node.id!r} may declare at most one output port")
        elif node.type == "subflow":
            _validate_subflow_node(node)

    # Edges: endpoints exist, ports exist, mappings are well-formed, loops bounded.
    # fed[(dst_node, dst_port)] counts feeding data edges — every input port needs
    # one; unconditional_fed counts only always-on (non-when, non-loop) feeders, so
    # two producers can't silently target the same port (the old shared-key clash).
    fed: dict[tuple[str, str], int] = {}
    unconditional_fed: dict[tuple[str, str], int] = {}
    # Dynamic fan-out (G1) topology: a fan-out worker is the dst of a fan_out edge;
    # loop_touched nodes appear on either end of a bounded-loop edge.  Used to reject
    # a worker inside a loop, nested fan-out, and a fan_in whose src isn't a worker.
    fan_out_workers = {e.dst for e in wf.edges if e.fan_out is not None}
    loop_touched = {e.src for e in wf.edges if e.loop_max is not None} | {
        e.dst for e in wf.edges if e.loop_max is not None
    }
    # v1 scope guards for subflow nodes: no subflow inside a loop or as a fan-out
    # worker (the loop×subflow and fan×subflow interactions are out of scope; the
    # fan-out limiter is the prerequisite for the latter).
    for _n in wf.nodes:
        if _n.type != "subflow":
            continue
        if _n.id in loop_touched:
            raise WorkflowValidationError(
                f"Subflow node {_n.id!r} may not be part of a bounded loop (v1)"
            )
        if _n.id in fan_out_workers:
            raise WorkflowValidationError(
                f"Subflow node {_n.id!r} may not be a fan-out worker (v1)"
            )
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
        # Strict interpolation for a condition: every {{ $.x }} operand must root at
        # an output port of the edge's source node (or a $in seed key).
        if edge.when is not None:
            for _root in _condition_operand_roots(edge.when):
                if _root not in src_outputs:
                    raise WorkflowValidationError(
                        f"Edge {edge.src!r}->{edge.dst!r}: condition references {{{{ ${_root} }}}} but "
                        f"{_root!r} is not an output port of {edge.src!r}"
                    )

        # Dynamic fan-out (G1) edge-shape validation.
        if edge.fan_out is not None:
            _validate_fan_out_edge(wf, edge, src_outputs, loop_touched)
        if edge.fan_in is not None:
            _validate_fan_in_edge(edge, fan_out_workers)
        # $output is a free-form sink: its destination "ports" are arbitrary output
        # keys, so only the source port must exist (no declared input ports to check,
        # and it never needs to be "fed").
        if edge.dst == OUT_NODE_ID:
            for sport, _dport in edge.mapping:
                head, subpath = _jsonpath_root(sport)
                if subpath:
                    # Sub-field mapping to the $output sink is not supported (only
                    # node->node edges resolve sub-fields); map the whole port.
                    raise WorkflowValidationError(
                        f"Edge {edge.src!r}->{OUT_NODE_ID}: sub-field key {sport!r} is not "
                        f"allowed into $output; map the whole port {head!r}"
                    )
                if head not in src_outputs:
                    raise WorkflowValidationError(
                        f"Edge {edge.src!r}->{edge.dst!r}: source has no output port {head!r}"
                    )
            continue

        dst_inputs = set(node_by_id[edge.dst].input_names)
        src_types = _output_port_types(wf, edge.src)  # empty for $in (untyped seed)
        src_schemas = _output_port_schemas(wf, edge.src)  # for sub-field type checks
        dst_types = {p.name: p.type for p in node_by_id[edge.dst].input_ports}
        for sport, dport in edge.mapping:
            # A JSONPath source key (``$.plan.owner``) reads a nested field of a
            # structured port; its root must be a real output port.
            head, subpath = _jsonpath_root(sport)
            if head not in src_outputs:
                raise WorkflowValidationError(
                    f"Edge {edge.src!r}->{edge.dst!r}: source has no output port {head!r}"
                )
            if dport not in dst_inputs:
                raise WorkflowValidationError(
                    f"Edge {edge.src!r}->{edge.dst!r}: destination has no input port {dport!r}"
                )
            # Edge type consistency: the resolved source type must match the
            # destination input type.  $in seeds are untyped (absent from
            # src_types/src_schemas) so edges out of $in are exempt.
            if dport in dst_types and (head in src_types or head in src_schemas):
                src_type: str | None
                if edge.fan_in is not None:
                    # fan_in (G1): the worker's port is aggregated into a list at
                    # runtime, so the destination must accept an array.
                    src_type = "array"
                elif edge.fan_out is not None and head == edge.fan_out and not subpath:
                    # fan_out (G1): the worker consumes ONE array element, so the
                    # element (items) type must match — not the array itself.
                    src_type = _schema_subtype(src_schemas[head], ["[]"]) if head in src_schemas else None
                elif not subpath:
                    src_type = src_types.get(head)
                else:
                    # 2b-2: descend the source port's schema along the JSONPath tail
                    # to find the sub-field type.  An un-typable path (wildcard,
                    # filter, scalar interior, missing field) yields None -> lenient.
                    steps = _jsonpath_tail_steps(sport)
                    src_type = (
                        _schema_subtype(src_schemas[head], steps)
                        if steps is not None and head in src_schemas
                        else None
                    )
                if src_type is not None and src_type != dst_types[dport]:
                    raise WorkflowValidationError(
                        f"Edge {edge.src!r}->{edge.dst!r}: type mismatch — source {sport!r} is "
                        f"{src_type!r} but destination port {dport!r} is {dst_types[dport]!r}"
                    )
            fed[(edge.dst, dport)] = fed.get((edge.dst, dport), 0) + 1
            if edge.when is None and edge.loop_max is None:
                unconditional_fed[(edge.dst, dport)] = unconditional_fed.get((edge.dst, dport), 0) + 1

        # back-edge (dst not strictly after src) must be a bounded loop
        if node_index[edge.dst] <= node_index[edge.src]:
            if not (edge.loop_max is not None and edge.loop_max >= 1):
                raise WorkflowValidationError(f"Back-edge {edge.src!r} -> {edge.dst!r} must have loop.max >= 1")

        # G6: a bounded loop with no `when` guard runs the full loop_max every time,
        # which is almost always an authoring mistake (a loop usually exits early on
        # a condition).  Warn, don't reject — an unconditional N-times loop is legal.
        if edge.loop_max is not None and edge.when is None:
            warnings.warn(
                f"Edge {edge.src!r} -> {edge.dst!r}: loop.max={edge.loop_max} with no 'when' guard "
                f"runs all {edge.loop_max} iterations unconditionally (usually a mistake — add a "
                f"'when' to exit early).",
                FlowWarning,
                stacklevel=2,
            )

    # Two unconditional producers into one input port is the old shared-key clash.
    for (dst, port), count in unconditional_fed.items():
        if count > 1:
            raise WorkflowValidationError(
                f"Node {dst!r}: input port {port!r} is fed by {count} unconditional edges "
                f"(ambiguous producer; use conditional edges if mutually exclusive)"
            )

    # Every declared input port must be fed by at least one edge mapping — unless
    # it is not required (e.g. a loop-carried value absent on the first pass).
    for node in wf.nodes:
        for p in node.input_ports:
            if not p.required:
                continue
            if fed.get((node.id, p.name), 0) == 0:
                raise WorkflowValidationError(
                    f"Node {node.id!r}: input port {p.name!r} is not fed by any edge mapping"
                )


def load_workflow(path: str | Path) -> WorkflowDef:
    """Load a WorkflowDef from a JSON file at *path*.

    A ``"subflow": "./child.json"`` path reference is resolved relative to *path*'s
    directory; the loaded child is inlined into the node (so execution, codegen, and
    serialization are unchanged).  A cyclic reference is rejected at load time.
    """
    p = Path(path).resolve()
    logger.debug("Loading workflow from %s", p)
    with p.open() as fh:
        data: dict[str, Any] = json.load(fh)
    wf = parse_workflow(data, base_dir=p.parent, _seen=frozenset({p}))
    validate_workflow(wf)
    return wf
