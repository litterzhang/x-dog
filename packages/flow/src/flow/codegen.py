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
from collections.abc import Callable

from flow.models import IN_NODE_ID, OUT_NODE_ID, Condition, EdgeDef, NodeDef, WorkflowDef

# A PortExpr resolves a referenced port name to a Python expression string.
PortExpr = Callable[[str], str]


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
    The assignment line is ``_OUT: dict[str, dict[str, str]] = <literal>`` (prefix
    34 chars); a long initial state can exceed 120, so a whole-line noqa is
    appended to keep the generated module ruff-clean.
    """
    pairs = ", ".join(f"{k!r}: {v!r}" for k, v in wf.initial_state)
    literal = "{" + f"{IN_NODE_ID!r}: {{{pairs}}}" + "}"
    if 34 + len(literal) > 120:
        return literal + "  # noqa: E501"
    return literal


def _render_prompt(prompt: str, port_expr: PortExpr) -> str:
    """Emit *prompt* with each ``{{port}}`` replaced by *port_expr(port)* in an f-string."""
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")
    if not pattern.search(prompt):
        return repr(prompt)
    fstr_body = pattern.sub(lambda m: "{" + port_expr(m.group(1)) + "}", prompt)
    escaped = fstr_body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return 'f"""' + escaped + '"""'


def _str_expr(s: str, port_expr: PortExpr) -> str:
    """Convert a possibly-interpolated condition operand to a Python expression."""
    single = re.compile(r"^\{\{\s*(\w+)\s*\}\}$")
    m = single.match(s.strip())
    if m:
        return port_expr(m.group(1))
    inter = re.compile(r"\{\{\s*(\w+)\s*\}\}")
    if inter.search(s):
        body = inter.sub(lambda x: "{" + port_expr(x.group(1)) + "}", s)
        escaped = body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return 'f"""' + escaped + '"""'
    return repr(s)


def _condition_to_expr(cond: Condition, port_expr: PortExpr) -> str:
    """Translate a Condition tree to a Python boolean expression (ports via *port_expr*)."""
    if cond.op == "equals":
        return f"{_str_expr(cond.value or '', port_expr)} == {_str_expr(cond.text or '', port_expr)}"
    if cond.op == "contains":
        return f"{_str_expr(cond.text or '', port_expr)} in {_str_expr(cond.value or '', port_expr)}"
    if cond.op == "not":
        return f"not ({_condition_to_expr(cond.children[0], port_expr)})"
    if cond.op == "and":
        return " and ".join(f"({_condition_to_expr(c, port_expr)})" for c in cond.children)
    if cond.op == "or":
        return " or ".join(f"({_condition_to_expr(c, port_expr)})" for c in cond.children)
    return "True"


def _condition_to_negated_expr(cond: Condition, port_expr: PortExpr) -> str:
    """Negation of :func:`_condition_to_expr`, emitted directly (ruff-clean break)."""
    if cond.op == "equals":
        return f"{_str_expr(cond.value or '', port_expr)} != {_str_expr(cond.text or '', port_expr)}"
    if cond.op == "contains":
        return f"{_str_expr(cond.text or '', port_expr)} not in {_str_expr(cond.value or '', port_expr)}"
    if cond.op == "not":
        return _condition_to_expr(cond.children[0], port_expr)
    if cond.op == "and":
        return " or ".join(f"({_condition_to_negated_expr(c, port_expr)})" for c in cond.children)
    if cond.op == "or":
        return " and ".join(f"({_condition_to_negated_expr(c, port_expr)})" for c in cond.children)
    return "False"


def _src_port_expr(safe_ids: dict[str, str], src: str) -> PortExpr:
    """Port resolver for an edge condition: reads the SOURCE node's output ports.

    Uses ``_OUT.get(node, {})`` so a guard that references a node which has not
    run yet (e.g. a self-loop's first-iteration check) yields '' instead of a
    ``KeyError``.
    """
    key = IN_NODE_ID if src == IN_NODE_ID else src
    return lambda port: f"_OUT.get({key!r}, {{}}).get({port!r}, '')"


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


def _render_ins(node: NodeDef, wf: WorkflowDef) -> list[str]:
    """Emit the ``ins`` dict assembling this node's input ports from incoming edges.

    Forward edges always feed; a loop back-edge feeds only when its ``when`` guard
    holds (evaluated against the source node's current output ports) — mirroring
    the executor's ``_build_inputs``.  A later edge writing the same input port
    wins, so a self-loop's fed-back value overrides the initial forward value.
    """
    if not node.input_ports:
        return []
    lines = ["    ins: dict[str, str] = {}"]
    for edge in _incoming(node.id, wf):
        src = IN_NODE_ID if edge.src == IN_NODE_ID else edge.src
        for sport, dport in edge.mapping:
            lines.append(f"    if {sport!r} in _OUT.get({src!r}, {{}}):")
            lines.append(f"        ins[{dport!r}] = _OUT[{src!r}][{sport!r}]")
    for edge in _incoming_loops(node.id, wf):
        if not edge.mapping:
            continue
        src = edge.src
        guard = (
            _condition_to_expr(edge.when, _src_port_expr({}, src)) if edge.when is not None else "True"
        )
        for sport, dport in edge.mapping:
            lines.append(f"    if ({guard}) and {sport!r} in _OUT.get({src!r}, {{}}):")
            lines.append(f"        ins[{dport!r}] = _OUT[{src!r}][{sport!r}]")
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
    out_port_expr: PortExpr = lambda port: f"_OUT[{node.id!r}].get({port!r}, '')"  # noqa: E731
    for edge in wf.edges:
        if edge.dst != OUT_NODE_ID or edge.src != node.id:
            continue
        guard = _condition_to_expr(edge.when, out_port_expr) if edge.when is not None else None
        for sport, okey in edge.mapping:
            cond = f"{sport!r} in _OUT[{node.id!r}]"
            if guard is not None:
                cond = f"({guard}) and {cond}"
            lines.append(f"    if {cond}:")
            lines.append(f"        _OUTPUT[{okey!r}] = _OUT[{node.id!r}][{sport!r}]")
    return lines


def _render_retry_wrapped(call_lines: list[str], node: NodeDef) -> list[str]:
    """Wrap *call_lines* (the core-work call) in a retry loop when node.retry is set.

    When ``node.retry`` is None or ``max == 0`` the call_lines are returned unchanged.
    The emitted loop mirrors the interpreter's semantics exactly.
    """
    if node.retry is None or node.retry.max == 0:
        return call_lines
    max_attempts = 1 + node.retry.max
    backoff = node.retry.backoff
    # call_lines each start with 4 spaces of indent (function body level).
    # We re-indent them to 12 spaces (inside `for` + `try:`).
    out: list[str] = []
    out.append("    _last_exc: BaseException | None = None")
    out.append(f"    for _attempt in range({max_attempts}):")
    out.append("        try:")
    for line in call_lines:
        # strip the leading 4 spaces then add 12
        out.append("            " + line.lstrip(" "))
    out.append("            _last_exc = None")
    out.append("            break")
    out.append("        except BaseException as _exc:")
    out.append("            _last_exc = _exc")
    out.append(f"            if _attempt + 1 < {max_attempts}:")
    out.append(f"                await asyncio.sleep({backoff} * (_attempt + 1))")
    out.append("    if _last_exc is not None:")
    out.append("        raise _last_exc")
    return out


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


def _render_isolation_handler(node_id: str, wf: WorkflowDef) -> list[str]:
    """Emit the except-block body for an on_error='isolate' node.

    Records the failure in ``_FAILED``, marks the node and its transitive
    successors in ``_ISOLATED``, and returns without re-raising.
    """
    lines: list[str] = []
    lines.append(f"        _FAILED[{node_id!r}] = f'{{type(_ev_exc).__name__}}: {{_ev_exc}}'")
    lines.append(f"        _ISOLATED.add({node_id!r})")
    for succ in _transitive_successors_ids(node_id, wf):
        lines.append(f"        _ISOLATED.add({succ!r})")
    lines.append("        return")
    return lines


def _render_script_node(node: NodeDef, fn_name: str, safe: str, wf: WorkflowDef) -> str:
    """Render a script node's async wrapper: build ins, call fn(ctx, **typed), store ports."""
    lines = [f"async def {fn_name}(provider: object) -> None:"]
    lines.append(f'    if {node.id!r} in _COMPLETED:')
    lines.append('        return')
    lines.append(f'    if {node.id!r} in _ISOLATED:')
    lines.append('        return')
    lines += _render_ins(node, wf)
    # Determinism memo hit guard — emitted only for deterministic nodes.
    if node.deterministic:
        ins_expr3 = "dict(ins)" if node.input_ports else "{}"
        lines.append(
            f'    _mk = f"{{{node.id!r}}}:{{hashlib.sha256(json.dumps(ins if {bool(node.input_ports)} else {{}}, '
            f'sort_keys=True).encode()).hexdigest()}}"  # noqa: E501'
        )
        lines.append('    if _mk in _MEMO:')
        lines.append(f'        _EVENT_LOG.info("NodeStarted node=%s step=%d", {node.id!r}, len(_STACK))')
        lines.append('        _t0_memo = time.monotonic()')
        lines.append(f'        _OUT[{node.id!r}] = dict(_MEMO[_mk])')
        lines.append(f'        _STACK.append({{"step": len(_STACK), "node": {node.id!r},'
                     f' "in": {ins_expr3}, "out": dict(_OUT[{node.id!r}])}})')
        lines.append(f'        _COMPLETED.add({node.id!r})')
        lines.append('        _save_checkpoint()')
        lines.append(
            f'        _EVENT_LOG.info("NodeFinished node=%s step=%d duration_s=%f",'
            f' {node.id!r}, len(_STACK) - 1, time.monotonic() - _t0_memo)  # noqa: E501'
        )
        lines.append('        return')
    lines.append(
        f'    _ctx = RuntimeContext(step=len(_STACK), node_id="{_ESC(node.id)}", workflow_name="{_ESC(wf.name)}")'
    )
    call_args = ["_ctx"]
    for p in node.input_ports:
        lines.append(f'    _in_{p.name} = to_python(ins.get({p.name!r}, ""), "{p.type}")')
        call_args.append(f"{p.name}=_in_{p.name}")
    await_kw = "await " if _script_is_async(node) else ""
    core_call = f"    _val = {await_kw}_script_{safe}({', '.join(call_args)})"
    # Emit start event, then wrap the work in try/except for timing.
    lines.append(f"    _EVENT_LOG.info('NodeStarted node=%s step=%d', {node.id!r}, len(_STACK))")
    lines.append("    _t0 = time.monotonic()")
    lines.append("    try:")
    # Wrap core call (and retry loop if any) one extra indent level deeper.
    for cl in _render_retry_wrapped([core_call], node):
        lines.append("    " + cl)
    # Output storage, frame, completed — still inside try.
    lines.append(f"        _OUT[{node.id!r}] = {{}}")
    if len(node.output_ports) <= 1:
        if node.output_ports:
            p = node.output_ports[0]
            lines.append(f'        _OUT[{node.id!r}][{p.name!r}] = to_state(_val, "{p.type}")')
    else:
        for p in node.output_ports:
            lines.append(f'        _OUT[{node.id!r}][{p.name!r}] = to_state(_val[{p.name!r}], "{p.type}")')
    for tl in _render_node_tail(node, wf):
        lines.append("    " + tl)
    lines.append(f'        _COMPLETED.add({node.id!r})')
    if node.deterministic:
        lines.append(f'        _MEMO[_mk] = dict(_OUT[{node.id!r}])')
    lines.append('        _save_checkpoint()')
    _finished_log = (
        f"        _EVENT_LOG.info('NodeFinished node=%s step=%d duration_s=%f', "
        f"{node.id!r}, len(_STACK) - 1, time.monotonic() - _t0)  # noqa: E501"
    )
    lines.append(_finished_log)
    lines.append("    except BaseException as _ev_exc:")
    _failed_log = (
        f"        _EVENT_LOG.info('NodeFailed node=%s step=%d duration_s=%f error=%s',"
        f" {node.id!r}, len(_STACK), time.monotonic() - _t0,"
        f" f'{{type(_ev_exc).__name__}}: {{_ev_exc}}')  # noqa: E501"
    )
    lines.append(_failed_log)
    if node.on_error == "isolate":
        lines += _render_isolation_handler(node.id, wf)
    else:
        lines.append("        raise")
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
    lines.append(f"    if {node.id!r} in _COMPLETED:")
    lines.append("        return")
    lines.append(f"    if {node.id!r} in _ISOLATED:")
    lines.append("        return")
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


def _render_node_function(node_id: str, wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    node_map = {n.id: n for n in wf.nodes}
    node = node_map[node_id]
    safe = safe_ids[node_id]
    fn_name = f"node_{safe}"

    if node.type == "script":
        return _render_script_node(node, fn_name, safe, wf)

    if node.type == "human":
        return _render_human_node(node, fn_name, wf)

    # agent node — prompts are PORT-LOCAL: {{x}} reads ins['x'].
    model = node.model or wf.default_model
    ins_expr: PortExpr = lambda port: f"ins.get({port!r}, '')"  # noqa: E731
    sys_prompt = _render_prompt(node.system_prompt, ins_expr)
    user_prompt = _render_prompt(node.prompt, ins_expr)

    lines = [f"async def {fn_name}(provider: object) -> None:"]
    lines.append(f"    if {node.id!r} in _COMPLETED:")
    lines.append("        return")
    lines.append(f"    if {node.id!r} in _ISOLATED:")
    lines.append("        return")
    lines += _render_ins(node, wf)
    # Determinism memo hit guard — emitted only for deterministic nodes.
    if node.deterministic:
        ins_expr3 = "dict(ins)" if node.input_ports else "{}"
        lines.append(
            f'    _mk = f"{{{node.id!r}}}:{{hashlib.sha256(json.dumps(ins if {bool(node.input_ports)} else {{}}, '
            f'sort_keys=True).encode()).hexdigest()}}"  # noqa: E501'
        )
        lines.append("    if _mk in _MEMO:")
        lines.append(f'        _EVENT_LOG.info("NodeStarted node=%s step=%d", {node.id!r}, len(_STACK))')
        lines.append("        _t0_memo = time.monotonic()")
        lines.append(f"        _OUT[{node.id!r}] = dict(_MEMO[_mk])")
        lines.append(f'        _STACK.append({{"step": len(_STACK), "node": {node.id!r},'
                     f' "in": {ins_expr3}, "out": dict(_OUT[{node.id!r}])}})')
        lines.append(f"        _COMPLETED.add({node.id!r})")
        lines.append("        _save_checkpoint()")
        lines.append(
            f'        _EVENT_LOG.info("NodeFinished node=%s step=%d duration_s=%f",'
            f' {node.id!r}, len(_STACK) - 1, time.monotonic() - _t0_memo)  # noqa: E501'
        )
        lines.append("        return")
    lines.append(f"    _sys = {_wrap_string_expr(sys_prompt)}")
    lines.append(f"    _usr = {_wrap_string_expr(user_prompt)}")
    if node.tools:
        tool_names = ", ".join(f'"{t}"' for t in node.tools)
        lines.append(f"    _tools = _REGISTRY.resolve(({tool_names},))")
        core_call = f'    result = await _run_agent(provider, "{model}", _sys, _usr, _tools)'
    else:
        core_call = f'    result = await _run_agent(provider, "{model}", _sys, _usr)'
    lines.append(f"    _EVENT_LOG.info('NodeStarted node=%s step=%d', {node.id!r}, len(_STACK))")
    lines.append("    _t0 = time.monotonic()")
    lines.append("    try:")
    for cl in _render_retry_wrapped([core_call], node):
        lines.append("    " + cl)
    lines.append(f"        _OUT[{node.id!r}] = {{}}")
    if node.output_ports:
        lines.append(f"        _OUT[{node.id!r}][{node.output_ports[0].name!r}] = result")
    for tl in _render_node_tail(node, wf):
        lines.append("    " + tl)
    lines.append(f"        _COMPLETED.add({node.id!r})")
    if node.deterministic:
        lines.append(f"        _MEMO[_mk] = dict(_OUT[{node.id!r}])")
    lines.append("        _save_checkpoint()")
    _finished_log = (
        f"        _EVENT_LOG.info('NodeFinished node=%s step=%d duration_s=%f', "
        f"{node.id!r}, len(_STACK) - 1, time.monotonic() - _t0)  # noqa: E501"
    )
    lines.append(_finished_log)
    lines.append("    except BaseException as _ev_exc:")
    _failed_log = (
        f"        _EVENT_LOG.info('NodeFailed node=%s step=%d duration_s=%f error=%s',"
        f" {node.id!r}, len(_STACK), time.monotonic() - _t0,"
        f" f'{{type(_ev_exc).__name__}}: {{_ev_exc}}')  # noqa: E501"
    )
    lines.append(_failed_log)
    if node.on_error == "isolate":
        lines += _render_isolation_handler(node.id, wf)
    else:
        lines.append("        raise")
    return "\n".join(lines)


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
    exprs = [f"({_condition_to_expr(e.when, _src_port_expr(safe_ids, e.src))})" for e in edges if e.when is not None]
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
            f"({_condition_to_expr(e.when, _src_port_expr(safe_ids, e.src))})" for e in edges if e.when is not None
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
            _condition_to_negated_expr(le.when, _src_port_expr(safe_ids, le.src)) if le.when is not None else None
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
            lines.append(f"{ind()}await node_{safe_ids[node_id]}(provider)")
            lines.append(f"{ind()}_ran.add('{_ESC(node_id)}')")
        else:
            lines.append(f"{ind()}if {gate}:")
            if loop_depth > 0:
                lines.append(f"{ind()}    _COMPLETED.discard({node_id!r})")
            lines.append(f"{ind()}    await node_{safe_ids[node_id]}(provider)")
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
    pending: list[str] = [wf.entry]
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
            lines.append(f"{ind()}await node_{safe_ids[ready[0]]}(provider)")
        else:
            for n in ready:
                if loop_depth > 0:
                    lines.append(f"{ind()}_COMPLETED.discard({n!r})")
            if use_capped:
                calls = [f"_capped(node_{safe_ids[n]}(provider))" for n in ready]
            else:
                calls = [f"node_{safe_ids[n]}(provider)" for n in ready]
            lines.append(f"{ind()}await asyncio.gather({', '.join(calls)})")

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
    """Emit imports for the tools registry, coercers/RuntimeContext, and run-ref scripts."""
    extra: dict[str, list[str]] = {}
    for node in wf.nodes:
        if node.type == "script" and node.run:
            module, func = node.run.rsplit(":", 1)
            safe = safe_ids[node.id]
            extra.setdefault(module, []).append(f"{func} as _script_{safe}")

    flow_tools_aliases = extra.pop("flow.tools", [])
    lines: list[str] = ["from flow.tools import default_registry"]

    # Custom-tool manifest: import each ref plus the bind_tool helper used to
    # register it under its manifest name (see _render_tool_registration).
    if wf.tool_refs:
        lines[0] = "from flow.tools import bind_tool, default_registry"
        for i, (_name, ref) in enumerate(wf.tool_refs):
            module, attr = ref.rsplit(":", 1)
            lines.append(f"from {module} import {attr} as _tool_{i}")

    script_nodes = [n for n in wf.nodes if n.type == "script"]
    if script_nodes:
        coercers = []
        if any(n.input_ports for n in script_nodes):
            coercers.append("to_python")
        if any(n.output_ports for n in script_nodes):
            coercers.append("to_state")
        if coercers:
            lines.insert(0, f"from flow.coerce import {', '.join(coercers)}")
        lines.insert(1 if coercers else 0, "from flow.runtime import RuntimeContext")

    for alias in flow_tools_aliases:
        lines.append(f"from flow.tools import {alias}")
    for module, aliases in sorted(extra.items()):
        for alias in aliases:
            lines.append(f"from {module} import {alias}")

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

    has_deterministic = any(n.deterministic for n in wf.nodes)
    hashlib_import = "import hashlib\n" if has_deterministic else ""

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
