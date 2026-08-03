"""flow.codegen — generate a self-contained Python module from a WorkflowDef.

Data flows through node-private ports: the generated module keeps a nested
``_OUT[node_id][port]`` store instead of a flat ``STATE``.  Each ``node_X``
assembles its input namespace ``ins`` from its incoming edge mappings
(``_OUT[src][src_port] -> ins[dst_port]``), runs, and writes its output ports
into ``_OUT[node_id]``.  Prompt ``{{x}}`` and script inputs are PORT-LOCAL (read
``ins``); an edge ``when`` reads the *source* node's output ports.
"""

from __future__ import annotations

import ast
import importlib.resources
import re
import string
from collections import defaultdict

from flow.models import (
    IN_NODE_ID,
    OUT_NODE_ID,
    Condition,
    EdgeDef,
    NodeDef,
    WorkflowDef,
    agent_is_structured,
    agent_output_schema,
    entry_frontier,
)

# A ContainerExpr is a Python expression string evaluating to the state dict
# (``dict[str, object]``) that a ``{{path}}`` placeholder is resolved against.
ContainerExpr = str
# Placeholder grammar: a dotted path of \w+ segments (mirrors flow.interpolate).
# A placeholder is ``{{ <jsonpath> }}`` — matches the interpreter's _PATTERN.
_PLACEHOLDER = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def _safe_id(node_id: str) -> str:
    """Convert a node id to a valid Python identifier."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", node_id)


def _safe_ids(wf: WorkflowDef) -> dict[str, str]:
    """Map every node id to a UNIQUE Python identifier.

    ``_safe_id`` alone collides when distinct ids normalise to the same symbol
    (e.g. ``a-b`` and ``a.b`` both become ``a_b``).  Collisions get a ``_2``/``_3``
    suffix.  Deterministic in ``wf.nodes`` order.
    """
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for node in wf.nodes:
        base = _safe_id(node.id)
        cand = base
        i = 2
        while cand in used:
            cand = f"{base}_{i}"
            i += 1
        used.add(cand)
        mapping[node.id] = cand
    return mapping


def _ESC(text: str) -> str:
    """Escape *text* for embedding inside a double-quoted string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _render_in_seed(wf: WorkflowDef) -> str:
    """Render the full ``_OUT`` seed dict literal: ``{'$in': {<initial_state>}}``.

    repr() escapes backslashes/quotes/newlines so the emitted literal is faithful.
    The assignment line is ``_OUT: dict[str, dict[str, object]] = <literal>`` (prefix
    37 chars); a long initial state can exceed 120, so a whole-line noqa is
    appended to keep the generated module ruff-clean.
    """
    pairs = ", ".join(f"{k!r}: {v!r}" for k, v in wf.initial_state)
    literal = "{" + f"{IN_NODE_ID!r}: {{{pairs}}}" + "}"
    if 37 + len(literal) > 120:
        return literal + "  # noqa: E501"
    return literal


def _interp_expr(template: str, container: ContainerExpr) -> str:
    """Compile an interpolated *template* to a Python str expression.

    Each ``{{path}}`` becomes ``interp_path(<container>, "path")`` — the SAME
    inlined helper the interpreter uses — so a structured value is projected to
    canonical JSON identically in both engines.  Literal spans are ``repr``'d and
    concatenated.  A template with no placeholder is just its ``repr``.
    """
    parts: list[str] = []
    pos = 0
    for m in _PLACEHOLDER.finditer(template):
        if m.start() > pos:
            parts.append(repr(template[pos : m.start()]))
        parts.append(f"interp_path({container}, {m.group(1)!r})")
        pos = m.end()
    if pos < len(template):
        parts.append(repr(template[pos:]))
    if not parts:
        return repr(template)
    return " + ".join(parts)


def _render_prompt(prompt: str, container: ContainerExpr) -> str:
    """Emit *prompt* as a Python str expression with ``{{path}}`` interpolated."""
    return _interp_expr(prompt, container)


def _str_expr(s: str, container: ContainerExpr) -> str:
    """Compile a condition operand to a projected-string Python expression.

    Both a bare ``{{x}}`` and an embedded placeholder go through ``interp_path``,
    so a condition always compares the canonical string projection on both sides —
    matching the interpreter, which routes every operand through ``interpolate``.
    """
    return _interp_expr(s, container)


_NUM_OP_PY = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _condition_to_expr(cond: Condition, container: ContainerExpr) -> str:
    """Translate a Condition tree to a Python boolean expression (ports via *container*)."""
    if cond.op == "equals":
        return f"{_str_expr(cond.value or '', container)} == {_str_expr(cond.text or '', container)}"
    if cond.op == "contains":
        return f"{_str_expr(cond.text or '', container)} in {_str_expr(cond.value or '', container)}"
    if cond.op in _NUM_OP_PY:
        left = _str_expr(cond.value or "", container)
        right = _str_expr(cond.text or "", container)
        return f"_ncmp({left}, {right}, {cond.op!r})"
    if cond.op == "not":
        return f"not ({_condition_to_expr(cond.children[0], container)})"
    if cond.op == "and":
        return " and ".join(f"({_condition_to_expr(c, container)})" for c in cond.children)
    if cond.op == "or":
        return " or ".join(f"({_condition_to_expr(c, container)})" for c in cond.children)
    return "True"


def _condition_to_negated_expr(cond: Condition, container: ContainerExpr) -> str:
    """Negation of :func:`_condition_to_expr`, emitted directly (ruff-clean break)."""
    if cond.op == "equals":
        return f"{_str_expr(cond.value or '', container)} != {_str_expr(cond.text or '', container)}"
    if cond.op == "contains":
        return f"{_str_expr(cond.text or '', container)} not in {_str_expr(cond.value or '', container)}"
    if cond.op in _NUM_OP_PY:
        left = _str_expr(cond.value or "", container)
        right = _str_expr(cond.text or "", container)
        return f"not _ncmp({left}, {right}, {cond.op!r})"
    if cond.op == "not":
        return _condition_to_expr(cond.children[0], container)
    if cond.op == "and":
        return " or ".join(f"({_condition_to_negated_expr(c, container)})" for c in cond.children)
    if cond.op == "or":
        return " and ".join(f"({_condition_to_negated_expr(c, container)})" for c in cond.children)
    return "False"


def _src_container_expr(safe_ids: dict[str, str], src: str) -> ContainerExpr:
    """State container for an edge condition: the SOURCE node's output ports.

    Uses ``_OUT.get(node, {})`` so a guard that references a node which has not
    run yet (e.g. a self-loop's first-iteration check) resolves to '' instead of
    a ``KeyError``.
    """
    key = IN_NODE_ID if src == IN_NODE_ID else src
    return f"_OUT.get({key!r}, {{}})"


def _wrap_string_expr(expr: str, indent: int = 4) -> str:
    """Return expr; append ``# noqa: E501`` if the assignment line would exceed 120 chars."""
    prefix = " " * indent
    if len(prefix) + 7 + len(expr) <= 120:
        return expr
    return expr + "  # noqa: E501"


def _script_is_async(node: NodeDef) -> bool:
    """Whether a script node's function is ``async`` (AST for inline; assume yes for run-ref)."""
    if node.code is None:
        return True
    tree = ast.parse(node.code)
    return any(isinstance(s, ast.AsyncFunctionDef) for s in tree.body)


def _incoming(node_id: str, wf: WorkflowDef) -> list[EdgeDef]:
    """Forward (non-loop) incoming edges of *node_id* in declaration order."""
    return [e for e in wf.edges if e.dst == node_id and e.loop_max is None]


def _incoming_loops(node_id: str, wf: WorkflowDef) -> list[EdgeDef]:
    """Loop back-edges feeding *node_id* (they supply data only while looping)."""
    return [e for e in wf.edges if e.dst == node_id and e.loop_max is not None]


def _emit_ins_assign(lines: list[str], src: str, sport: str, dport: str, guard: str | None) -> None:
    """Emit the guarded ``ins[dport] = <source value>`` for one edge mapping.

    The source key is a JSONPath resolved against the source node's output-ports
    dict via the inlined ``jsonpath_get`` — a plain port (``plan``) or a nested
    field (``$.verdict.within_budget``).  A miss (None) leaves the port unfed —
    mirroring the interpreter's ``_build_inputs``.  *guard* is an extra condition
    (loop edges) or None.
    """
    getter = f"jsonpath_get(_OUT.get({src!r}, {{}}), {sport!r})"
    cond = f"({guard})" if guard else None
    lines.append(f"    _rv = {getter}" if cond is None else f"    if {cond}:")
    if cond is None:
        lines.append("    if _rv is not None:")
        lines.append(f"        ins[{dport!r}] = _rv")
    else:
        lines.append(f"        _rv = {getter}")
        lines.append("        if _rv is not None:")
        lines.append(f"            ins[{dport!r}] = _rv")


def _render_ins(node: NodeDef, wf: WorkflowDef) -> list[str]:
    """Emit the ``ins`` dict assembling this node's input ports from incoming edges.

    Forward edges always feed; a loop back-edge feeds only when its ``when`` guard
    holds (evaluated against the source node's current output ports) — mirroring
    the executor's ``_build_inputs``.  A later edge writing the same input port
    wins, so a self-loop's fed-back value overrides the initial forward value.
    """
    if not node.input_ports:
        return []
    lines = ["    ins: dict[str, object] = {}"]
    for edge in _incoming(node.id, wf):
        src = IN_NODE_ID if edge.src == IN_NODE_ID else edge.src
        for sport, dport in edge.mapping:
            _emit_ins_assign(lines, src, sport, dport, None)
    for edge in _incoming_loops(node.id, wf):
        if not edge.mapping:
            continue
        src = edge.src
        guard = (
            _condition_to_expr(edge.when, _src_container_expr({}, src)) if edge.when is not None else "True"
        )
        for sport, dport in edge.mapping:
            _emit_ins_assign(lines, src, sport, dport, guard)
    return lines


def _render_node_tail(node: NodeDef, wf: WorkflowDef) -> list[str]:
    """Emit the per-node epilogue: push a trace frame + flush any ``$output`` edges.

    Mirrors the executor's ``_record_frame``: the step is ``len(_STACK)`` (one frame
    per node run), the frame records this node's assembled ``ins`` and its output
    ports, and any edge to ``$output`` copies the mapped keys into ``_OUTPUT`` (a
    ``when`` guard is evaluated against this node's output ports).
    """
    ins_expr = "dict(ins)" if node.input_ports else "{}"
    frame = (
        f"{{'step': len(_STACK), 'node': {node.id!r}, "
        f"'in': {ins_expr}, 'out': dict(_OUT[{node.id!r}])}}"
    )
    frame_line = f"    _STACK.append({frame})"
    if len(frame_line) > 120:
        frame_line += "  # noqa: E501"
    lines = [frame_line]
    out_container: ContainerExpr = f"_OUT[{node.id!r}]"
    for edge in wf.edges:
        if edge.dst != OUT_NODE_ID or edge.src != node.id:
            continue
        guard = _condition_to_expr(edge.when, out_container) if edge.when is not None else None
        for sport, okey in edge.mapping:
            cond = f"{sport!r} in _OUT[{node.id!r}]"
            if guard is not None:
                cond = f"({guard}) and {cond}"
            lines.append(f"    if {cond}:")
            lines.append(f"        _OUTPUT[{okey!r}] = _OUT[{node.id!r}][{sport!r}]")
    return lines


def _retry_spec(node: NodeDef, wf: WorkflowDef) -> str:
    """Emit the keyword args passed to ``_drive`` carrying this node's policy."""
    max_attempts = 1 + (node.retry.max if node.retry is not None else 0)
    backoff = node.retry.backoff if node.retry is not None else 0.0
    isolate = node.on_error == "isolate"
    succs = tuple(_transitive_successors_ids(node.id, wf)) if isolate else ()
    return (
        f"retry_max={max_attempts}, backoff={backoff!r}, deterministic={node.deterministic!r}, "
        f"isolate={isolate!r}, isolated_succs={succs!r}"
    )


def _incoming_fan_out_edge(node_id: str, wf: WorkflowDef) -> EdgeDef | None:
    """The single fan_out edge feeding *node_id*, or None (loader forbids >1)."""
    for e in wf.edges:
        if e.dst == node_id and e.fan_out is not None:
            return e
    return None


def _fan_worker_port(edge: EdgeDef) -> str:
    """The worker input port that receives one array element (the fan_out mapping's dst)."""
    for sport, dport in edge.mapping:
        head = sport[2:] if sport.startswith("$.") else (sport[1:] if sport.startswith("$") else sport)
        head = re.split(r"[.\[]", head, maxsplit=1)[0]
        if head == edge.fan_out:
            return dport
    return ""


def _invoke_expr(node_id: str, wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """The awaitable expression that runs one node from the scheduler.

    Script/agent nodes go through the generic ``_drive`` (guards, inputs, retry,
    store, memo, budget, checkpoint, isolation); a fan-out worker goes through
    ``_drive_fan`` (run once per array element, aggregate ports into lists); a
    human node stays a self-contained call (it pauses via SystemExit)."""
    node = next(n for n in wf.nodes if n.id == node_id)
    safe = safe_ids[node_id]
    if node.type == "human":
        return f"node_{safe}(provider)"
    fan_edge = _incoming_fan_out_edge(node_id, wf)
    if fan_edge is not None:
        fan_port = _fan_worker_port(fan_edge)
        out_ports = tuple(p.name for p in node.output_ports)
        return (
            f"_drive_fan({node_id!r}, node_{safe}, _inputs_{safe}, _store_{safe}, "
            f"{fan_port!r}, {out_ports!r})"
        )
    return (
        f'_drive({node_id!r}, node_{safe}, _inputs_{safe}, _store_{safe}, {_retry_spec(node, wf)})'
    )


def _await_line(indent: str, node_id: str, wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """An ``await <invoke>`` scheduler line, with a whole-line noqa when too long."""
    line = f"{indent}await {_invoke_expr(node_id, wf, safe_ids)}"
    return line if len(line) <= 120 else line + "  # noqa: E501"


def _render_inputs_fn(node: NodeDef, wf: WorkflowDef, safe: str) -> list[str]:
    """Emit ``_inputs_<safe>() -> dict`` — assembles this node's ins from _OUT."""
    lines = [f"def _inputs_{safe}() -> dict[str, object]:"]
    body = _render_ins(node, wf)
    if not body:
        lines.append("    return {}")
        return lines
    lines += body
    lines.append("    return ins")
    return lines


def _render_store_fn(node: NodeDef, wf: WorkflowDef, safe: str) -> list[str]:
    """Emit ``_store_<safe>(ins, _res) -> None`` — write _OUT + flush $output.

    *_res* is the pure node function's returned output-port dict; it is copied
    into ``_OUT[node]`` verbatim (the node fn already coerced/fanned per port),
    then any ``$output`` edge is flushed.  The ``ins`` param feeds the $output
    ``when`` guards' unused-arg-free signature and keeps parity with the frame.
    """
    lines = [f"def _store_{safe}(ins: dict[str, object], _res: dict[str, object]) -> None:"]
    lines.append(f"    _OUT[{node.id!r}] = dict(_res)")
    # $output flush (mirrors _render_node_tail's second half).
    out_container: ContainerExpr = f"_OUT[{node.id!r}]"
    flushed = False
    for edge in wf.edges:
        if edge.dst != OUT_NODE_ID or edge.src != node.id:
            continue
        guard = _condition_to_expr(edge.when, out_container) if edge.when is not None else None
        for sport, okey in edge.mapping:
            cond = f"{sport!r} in _OUT[{node.id!r}]"
            if guard is not None:
                cond = f"({guard}) and {cond}"
            lines.append(f"    if {cond}:")
            lines.append(f"        _OUTPUT[{okey!r}] = _OUT[{node.id!r}][{sport!r}]")
            flushed = True
    if not flushed:
        lines.append("    _ = ins  # (no $output flush; ins unused)")
    return lines


def _transitive_successors_ids(node_id: str, wf: WorkflowDef) -> list[str]:
    """Compute transitive successors of *node_id* in the forward graph (code-gen time)."""
    visited: list[str] = []
    seen: set[str] = set()
    queue = [node_id]
    while queue:
        cur = queue.pop()
        for e in wf.edges:
            if e.src != cur or e.loop_max is not None or e.dst == OUT_NODE_ID:
                continue
            if e.dst not in seen:
                seen.add(e.dst)
                visited.append(e.dst)
                queue.append(e.dst)
    return visited


def _entry_guards(node_id: str) -> list[str]:
    """The two early-return guards every node wrapper opens with (already-done / isolated)."""
    return [
        f"    if {node_id!r} in _COMPLETED:",
        "        return",
        f"    if {node_id!r} in _ISOLATED:",
        "        return",
    ]


def _render_script_node(node: NodeDef, fn_name: str, safe: str, wf: WorkflowDef) -> str:
    """Render a script node as a pure function: (provider, ctx, **inputs) -> (outputs, 0).

    Assembles typed kwargs from its input params, calls the inlined ``_script_X``,
    coerces the return into the output-port dict, and returns ``(outputs, 0)``.
    Storing/retry/events are the driver's job.
    """
    params = ["provider: object", "ctx: RuntimeContext"]
    params += [f"{p.name}: object" for p in node.input_ports]
    sig = f"async def {fn_name}({', '.join(params)}) -> tuple[dict[str, object], int]:"
    lines = [sig if len(sig) <= 120 else sig + "  # noqa: E501"]
    call_args = ["ctx"]
    for p in node.input_ports:
        lines.append(f'    _in_{p.name} = to_python({p.name}, "{p.type}")')
        call_args.append(f"{p.name}=_in_{p.name}")
    await_kw = "await " if _script_is_async(node) else ""
    core = f"    _val = {await_kw}_script_{safe}({', '.join(call_args)})"
    lines.append(core if len(core) <= 120 else core + "  # noqa: E501")
    lines.append("    _out: dict[str, object] = {}")
    if len(node.output_ports) <= 1:
        if node.output_ports:
            p = node.output_ports[0]
            lines.append(f'    _out[{p.name!r}] = to_state(_val, "{p.type}")')
    else:
        for p in node.output_ports:
            store = f'    _out[{p.name!r}] = to_state(_val[{p.name!r}], "{p.type}")'
            lines.append(store if len(store) <= 120 else store + "  # noqa: E501")
    lines.append("    return _out, 0")
    return "\n".join(lines)


def _rename_inline_fn(code: str, alias: str) -> str:
    """Return *code* with its single top-level function renamed to *alias*."""
    tree = ast.parse(code)
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            stmt.name = alias
            break
    return ast.unparse(tree)


def _render_inline_scripts(wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Emit each inline-``code`` script node's function as a top-level ``_script_<id>``."""
    blocks: list[str] = []
    for node in wf.nodes:
        if node.type == "script" and node.code is not None:
            alias = f"_script_{safe_ids[node.id]}"
            blocks.append(_rename_inline_fn(node.code, alias))
    return "\n\n\n".join(blocks)


def _render_human_node(node: NodeDef, fn_name: str, wf: WorkflowDef) -> str:
    """Render a human node's async wrapper: pass if signal delivered, else pause via SystemExit."""
    lines = [f"async def {fn_name}(provider: object) -> None:"]
    lines += _entry_guards(node.id)
    lines += _render_ins(node, wf)
    lines.append(f"    if {node.signal!r} in _SIGNALS:")
    lines.append(f"        _EVENT_LOG.info('NodeStarted node=%s step=%d', {node.id!r}, len(_STACK))")
    lines.append("        _t0 = time.monotonic()")
    lines.append(f"        _OUT[{node.id!r}] = {{}}")
    if node.output_ports:
        p = node.output_ports[0]
        lines.append(f"        _OUT[{node.id!r}][{p.name!r}] = 'approved'")
    for tl in _render_node_tail(node, wf):
        lines.append("    " + tl)
    lines.append(f"        _COMPLETED.add({node.id!r})")
    lines.append("        _save_checkpoint()")
    _finished_log = (
        f"        _EVENT_LOG.info('NodeFinished node=%s step=%d duration_s=%f', "
        f"{node.id!r}, len(_STACK) - 1, time.monotonic() - _t0)  # noqa: E501"
    )
    lines.append(_finished_log)
    lines.append("    else:")
    lines.append("        _save_checkpoint()")
    lines.append(
        f"        print(f'PAUSED: {_ESC(node.id)} awaiting {_ESC(node.signal)}')"
    )
    lines.append(f"        raise SystemExit(f'PAUSED: {_ESC(node.id)} awaiting {_ESC(node.signal)}')")
    return "\n".join(lines)


def _render_agent_node(node: NodeDef, fn_name: str, wf: WorkflowDef) -> str:
    """Render an agent node as a pure function: (provider, ctx, **inputs) -> (outputs, tokens).

    Reconstructs a local ``ins`` from its params (so ``{{$.x}}`` prompt
    interpolation is unchanged), runs the agent once, and coerces the result into
    the output-port dict.  Storing/retry/budget/events are the driver's job.
    """
    model = node.model or wf.default_model
    params = ["provider: object", "ctx: RuntimeContext"]
    params += [f"{p.name}: object" for p in node.input_ports]
    sig = f"async def {fn_name}({', '.join(params)}) -> tuple[dict[str, object], int]:"
    lines = [sig if len(sig) <= 120 else sig + "  # noqa: E501"]
    if node.input_ports:
        pairs = ", ".join(f"{p.name!r}: {p.name}" for p in node.input_ports)
        ins_line = f"    ins: dict[str, object] = {{{pairs}}}"
        lines.append(ins_line if len(ins_line) <= 120 else ins_line + "  # noqa: E501")
        ins_container: ContainerExpr = "ins"
    else:
        ins_container = "{}"
    sys_prompt = _render_prompt(node.system_prompt, ins_container)
    user_prompt = _render_prompt(node.prompt, ins_container)
    lines.append(f"    _sys = {_wrap_string_expr(sys_prompt)}")
    lines.append(f"    _usr = {_wrap_string_expr(user_prompt)}")
    _call_args = [f'provider, "{model}", _sys, _usr']
    if node.tools:
        tool_names = ", ".join(f'"{t}"' for t in node.tools)
        lines.append(f"    _tools = _REGISTRY.resolve(({tool_names},))")
        _call_args.append("_tools")
    _structured = agent_is_structured(node)
    if _structured:
        _call_args.append(f"output_schema={agent_output_schema(node)!r}")
    if node.web_search:
        _call_args.append(f'web_search_model="{_ESC(node.web_search_model or model)}"')
    _core = f"    result, _node_tokens = await _run_agent({', '.join(_call_args)})"
    lines.append(_core if len(_core) <= 120 else _core + "  # noqa: E501")
    lines.append("    _out: dict[str, object] = {}")
    if node.output_ports:
        if _structured and len(node.output_ports) > 1:
            _not_obj_msg = f"Node {node.id!r}: multi-output agent must submit an object"
            lines.append("    if not isinstance(result, dict):")
            lines.append(f"        raise WorkflowExecutionError({_not_obj_msg!r})")
            for p in node.output_ports:
                _miss_msg = f"Node {node.id!r}: submitted result is missing field {p.name!r}"
                lines.append(f"    if {p.name!r} not in result:")
                lines.append(f"        raise WorkflowExecutionError({_miss_msg!r})")
                store = f"    _out[{p.name!r}] = to_state(result[{p.name!r}], {p.type!r})"
                lines.append(store if len(store) <= 120 else store + "  # noqa: E501")
        else:
            lines.append(f"    _out[{node.output_ports[0].name!r}] = result")
    lines.append("    return _out, _node_tokens")
    return "\n".join(lines)


def _render_node_function(node_id: str, wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Emit a node's code: its pure function plus, for script/agent, the driver
    helpers ``_inputs_<safe>`` and ``_store_<safe>``.  Human nodes stay
    self-contained (they pause via SystemExit and don't fit the retry driver)."""
    node_map = {n.id: n for n in wf.nodes}
    node = node_map[node_id]
    safe = safe_ids[node_id]
    fn_name = f"node_{safe}"

    if node.type == "human":
        return _render_human_node(node, fn_name, wf)

    if node.type == "script":
        node_fn = _render_script_node(node, fn_name, safe, wf)
    else:
        node_fn = _render_agent_node(node, fn_name, wf)
    blocks = [
        node_fn,
        "\n".join(_render_inputs_fn(node, wf, safe)),
        "\n".join(_render_store_fn(node, wf, safe)),
    ]
    return "\n\n\n".join(blocks)


def _build_fwd_graph(
    wf: WorkflowDef,
) -> tuple[dict[str, list[EdgeDef]], dict[str, list[str]], list[EdgeDef]]:
    """Split edges into forward (non-loop) and loop back-edges.

    ``$in`` is excluded from predecessor tracking (it is always available), so a
    node fed only by ``$in`` behaves like an entry node.
    """
    fwd_from: dict[str, list[EdgeDef]] = defaultdict(list)
    fwd_preds: dict[str, list[str]] = defaultdict(list)
    loop_edges: list[EdgeDef] = []
    for e in wf.edges:
        if e.dst == OUT_NODE_ID:
            continue  # the $output sink is flushed in each node's tail, not scheduled
        if e.loop_max is None:
            fwd_from[e.src].append(e)
            if e.src != IN_NODE_ID:
                fwd_preds[e.dst].append(e.src)
        else:
            loop_edges.append(e)
    return fwd_from, fwd_preds, loop_edges


def _cond_in_edges(node_id: str, wf: WorkflowDef) -> list[EdgeDef]:
    """Forward incoming edges used to compute run guards.

    Excludes ``$in`` edges: the source node is always available and only supplies
    data, so it never makes a node "unconditionally reached" (that is decided by
    real predecessor nodes / their conditions), mirroring the executor's
    scheduling where ``$in`` is not a predecessor.
    """
    return [e for e in wf.edges if e.dst == node_id and e.loop_max is None and e.src != IN_NODE_ID]


def _node_guard(node_id: str, wf: WorkflowDef, safe_ids: dict[str, str]) -> str | None:
    """Guard expression if *node_id* is reached ONLY via conditional edges, else None."""
    edges = _cond_in_edges(node_id, wf)
    if not edges:
        return None
    if any(e.when is None for e in edges):
        return None
    exprs = [
        f"({_condition_to_expr(e.when, _src_container_expr(safe_ids, e.src))})"
        for e in edges
        if e.when is not None
    ]
    return " or ".join(exprs) if len(exprs) > 1 else exprs[0]


def _node_run_gate(node_id: str, wf: WorkflowDef, fwd_preds: dict[str, list[str]], safe_ids: dict[str, str]) -> str:
    """Boolean expression under which *node_id* runs (mirrors the executor)."""
    preds = fwd_preds.get(node_id, [])
    edges = _cond_in_edges(node_id, wf)
    if not edges:
        return "True"
    clauses: list[str] = [f"'{_ESC(p)}' in _ran" for p in dict.fromkeys(preds)]
    if not any(e.when is None for e in edges):
        guards = [
            f"({_condition_to_expr(e.when, _src_container_expr(safe_ids, e.src))})" for e in edges if e.when is not None
        ]
        clauses.append(f"({' or '.join(guards)})" if len(guards) > 1 else guards[0])
    return " and ".join(clauses) if clauses else "True"


def _has_forward_conditional(wf: WorkflowDef) -> bool:
    """True if the workflow has a conditional edge that is NOT a loop back-edge."""
    return any(e.when is not None and e.loop_max is None for e in wf.edges)


def _render_main_body(wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Body of ``async def main()`` — conditional-aware when forward guards exist."""
    if _has_forward_conditional(wf):
        return _render_main_body_conditional(wf, safe_ids)
    return _render_main_body_waves(wf, safe_ids, use_capped=wf.max_concurrency > 0)


def _loop_maps(
    loop_edges: list[EdgeDef], safe_ids: dict[str, str]
) -> tuple[dict[str, tuple[str, int | None]], set[str], dict[str, str | None]]:
    """Shared loop bookkeeping: entry->exit/max, exit set, exit->break expression."""
    entry_map: dict[str, tuple[str, int | None]] = {}
    exit_set: set[str] = set()
    exit_break: dict[str, str | None] = {}
    for le in loop_edges:
        entry_map[le.dst] = (le.src, le.loop_max)
        exit_set.add(le.src)
        exit_break[le.src] = (
            _condition_to_negated_expr(le.when, _src_container_expr(safe_ids, le.src)) if le.when is not None else None
        )
    return entry_map, exit_set, exit_break


def _render_main_body_conditional(wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Emit ``_ran``-gated sequential code for workflows with forward conditionals."""
    fwd_from, fwd_preds, loop_edges = _build_fwd_graph(wf)
    loop_entry_map, loop_exit_set, loop_exit_break = _loop_maps(loop_edges, safe_ids)

    # Topological order over forward edges (Kahn), stable in declaration order.
    indeg: dict[str, int] = {n.id: 0 for n in wf.nodes}
    for e in wf.edges:
        if e.loop_max is None and e.src != IN_NODE_ID and e.dst != OUT_NODE_ID:
            indeg[e.dst] = indeg.get(e.dst, 0) + 1
    order: list[str] = []
    queue = [n.id for n in wf.nodes if indeg[n.id] == 0]
    seen: set[str] = set(queue)
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for e in fwd_from.get(cur, []):
            if e.src == IN_NODE_ID:
                continue
            indeg[e.dst] -= 1
            if indeg[e.dst] == 0 and e.dst not in seen:
                seen.add(e.dst)
                queue.append(e.dst)
    for n in wf.nodes:
        if n.id not in order:
            order.append(n.id)

    lines: list[str] = []
    loop_depth = 0

    def ind() -> str:
        return "    " * (1 + loop_depth)

    lines.append(f"{ind()}_ran: set[str] = set()")

    for node_id in order:
        if node_id in loop_entry_map:
            _, lmax = loop_entry_map[node_id]
            lines.append(f"{ind()}for _loop_i in range({lmax}):")
            loop_depth += 1

        gate = _node_run_gate(node_id, wf, fwd_preds, safe_ids)
        if gate == "True":
            if loop_depth > 0:
                lines.append(f"{ind()}_COMPLETED.discard({node_id!r})")
            lines.append(_await_line(ind(), node_id, wf, safe_ids))
            lines.append(f"{ind()}_ran.add('{_ESC(node_id)}')")
        else:
            lines.append(f"{ind()}if {gate}:")
            if loop_depth > 0:
                lines.append(f"{ind()}    _COMPLETED.discard({node_id!r})")
            lines.append(_await_line(ind() + "    ", node_id, wf, safe_ids))
            lines.append(f"{ind()}    _ran.add('{_ESC(node_id)}')")

        if node_id in loop_exit_set:
            break_cond = loop_exit_break.get(node_id)
            if break_cond is not None:
                lines.append(f"{ind()}if {break_cond}:")
                lines.append(f"{ind()}    break")
            loop_depth -= 1

    return "\n".join(lines)


def _render_main_body_waves(wf: WorkflowDef, safe_ids: dict[str, str], use_capped: bool = False) -> str:
    """BFS-wave body: gather for parallel fan-out, for-range for bounded loops."""
    fwd_from, fwd_preds, loop_edges = _build_fwd_graph(wf)
    loop_entry_map, loop_exit_set, loop_exit_break = _loop_maps(loop_edges, safe_ids)

    completed: set[str] = set()
    pending: list[str] = list(entry_frontier(wf))
    lines: list[str] = []
    loop_depth = 0

    def ind() -> str:
        return "    " * (1 + loop_depth)

    while pending:
        ready = [n for n in pending if all(p in completed for p in fwd_preds.get(n, []))]
        if not ready:
            break
        for n in ready:
            pending.remove(n)

        for n in ready:
            if n in loop_entry_map:
                _, lmax = loop_entry_map[n]
                lines.append(f"{ind()}for _loop_i in range({lmax}):")
                loop_depth += 1
                break

        if len(ready) == 1:
            if loop_depth > 0:
                lines.append(f"{ind()}_COMPLETED.discard({ready[0]!r})")
            lines.append(_await_line(ind(), ready[0], wf, safe_ids))
        else:
            for n in ready:
                if loop_depth > 0:
                    lines.append(f"{ind()}_COMPLETED.discard({n!r})")
            if use_capped:
                calls = [f"_capped({_invoke_expr(n, wf, safe_ids)})" for n in ready]
            else:
                calls = [_invoke_expr(n, wf, safe_ids) for n in ready]
            gather = f"{ind()}await asyncio.gather({', '.join(calls)})"
            lines.append(gather if len(gather) <= 120 else gather + "  # noqa: E501")

        for n in ready:
            completed.add(n)

        for n in ready:
            if n in loop_exit_set:
                break_cond = loop_exit_break.get(n)
                if break_cond is not None:
                    lines.append(f"{ind()}if {break_cond}:")
                    lines.append(f"{ind()}    break")
                loop_depth -= 1
                break

        for n in ready:
            for e in fwd_from.get(n, []):
                succ = e.dst
                if succ not in completed and succ not in pending:
                    pending.append(succ)

    return "\n".join(lines)


def _render_script_imports(wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Emit imports for custom-tool manifest refs and run-ref scripts.

    The flow-internal helpers (errors / coerce / runtime) AND the tool registry
    (ToolRegistry / default_registry / bind_tool) are inlined into the template,
    so the generated module never imports the ``flow`` package for them. Only
    external references remain: a workflow's custom-tool manifest ``module:attr``
    entries and its script nodes' ``run: module:func`` references.
    """
    extra: dict[str, list[str]] = {}
    for node in wf.nodes:
        if node.type == "script" and node.run:
            module, func = node.run.rsplit(":", 1)
            safe = safe_ids[node.id]
            extra.setdefault(module, []).append(f"{func} as _script_{safe}")

    flow_tools_aliases = extra.pop("flow.tools", [])
    lines: list[str] = []

    # Custom-tool manifest: import each ref.
    if wf.tool_refs:
        for i, (_name, ref) in enumerate(wf.tool_refs):
            module, attr = ref.rsplit(":", 1)
            lines.append(f"from {module} import {attr} as _tool_{i}")

    for alias in flow_tools_aliases:
        lines.append(f"from flow.tools import {alias}")
    for module, aliases in sorted(extra.items()):
        for alias in aliases:
            lines.append(f"from {module} import {alias}")

    if not lines:
        return ""
    # Sort all lines to satisfy ruff import ordering within the third-party block.
    lines = sorted(lines)

    return "\n".join(lines) + "\n"


def _render_tool_registration(wf: WorkflowDef) -> str:
    """Emit ``_REGISTRY.register(...)`` calls for the custom-tool manifest.

    Returns an empty string when there is no manifest, so the template line stays
    ``_REGISTRY = default_registry()`` unchanged.  Each ref (imported as
    ``_tool_i`` by :func:`_render_script_imports`) is coerced to an AgentTool and
    re-named to its manifest key, mirroring ``register_workflow_tools``.
    """
    if not wf.tool_refs:
        return ""
    lines = [""]
    for i, (name, _ref) in enumerate(wf.tool_refs):
        lines.append(f"_REGISTRY.register(bind_tool(_tool_{i}, {name!r}))")
    return "\n".join(lines)


def _render_concurrency_boilerplate(wf: WorkflowDef) -> str:
    """Emit _SEM + _capped helper when max_concurrency > 0, else empty string."""
    if wf.max_concurrency <= 0:
        return ""
    cap = wf.max_concurrency
    lines = [
        "",
        "from collections.abc import Awaitable as _Awaitable",
        "",
        f"_SEM: asyncio.Semaphore | None = asyncio.Semaphore({cap})",
        "",
        "",
        "async def _capped(coro: \"_Awaitable[None]\") -> None:",
        "    if _SEM is None:",
        "        await coro",
        "    else:",
        "        async with _SEM:",
        "            await coro",
    ]
    return "\n".join(lines)


def _has_human_nodes(wf: WorkflowDef) -> bool:
    """True if any node in the workflow is a human node."""
    return any(n.type == "human" for n in wf.nodes)


def generate(wf: WorkflowDef) -> str:
    """Generate a complete Python module string for the given WorkflowDef."""
    tmpl_path = importlib.resources.files("flow") / "templates" / "runtime.py.tmpl"
    tmpl_text = tmpl_path.read_text(encoding="utf-8")

    safe_ids = _safe_ids(wf)

    inline_scripts = _render_inline_scripts(wf, safe_ids)
    node_functions = "\n\n\n".join(_render_node_function(n.id, wf, safe_ids) for n in wf.nodes)
    if inline_scripts:
        node_functions = inline_scripts + "\n\n\n" + node_functions
    main_body = _render_main_body(wf, safe_ids)
    concurrency_boilerplate = _render_concurrency_boilerplate(wf)

    if _has_human_nodes(wf):
        signals_boilerplate = (
            "\n_SIGNALS: set[str] = set()\n"
        )
        signals_main_init = (
            "    _raw_signals = os.environ.get('FLOW_SIGNALS', '')\n"
            "    _SIGNALS.update(s.strip() for s in _raw_signals.split(',') if s.strip())"
        )
    else:
        signals_boilerplate = "\n_SIGNALS: set[str] = set()"
        signals_main_init = ""

    # _drive always references hashlib in its (deterministic-only) memo branch.
    hashlib_import = "import hashlib\n"

    result = string.Template(tmpl_text).substitute(
        workflow_name=wf.name,
        in_seed=_render_in_seed(wf),
        in_node_id=repr(IN_NODE_ID),
        node_functions=node_functions,
        provider=wf.provider,
        main_body=main_body,
        script_imports=_render_script_imports(wf, safe_ids),
        tool_registration=_render_tool_registration(wf),
        concurrency_boilerplate=concurrency_boilerplate,
        signals_boilerplate=signals_boilerplate,
        signals_main_init=signals_main_init,
        hashlib_import=hashlib_import,
    )
    return result
