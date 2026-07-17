"""flow.codegen — generate a self-contained Python module from a WorkflowDef."""

from __future__ import annotations

import ast
import importlib.resources
import re
import string
from collections import defaultdict

from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef


def _safe_id(node_id: str) -> str:
    """Convert a node id to a valid Python identifier."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", node_id)


def _ESC(text: str) -> str:
    """Escape *text* for embedding inside a double-quoted string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _render_initial_state(wf: WorkflowDef) -> str:
    pairs = ", ".join(f'"{k}": "{v}"' for k, v in wf.initial_state)
    return "{" + pairs + "}"


def _render_prompt(prompt: str) -> str:
    """Emit prompt with {{key}} replaced by STATE['key'] lookups as an f-string."""
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")
    keys = pattern.findall(prompt)
    if not keys:
        return repr(prompt)
    fstr_body = pattern.sub(lambda m: "{STATE['" + m.group(1) + "']}", prompt)
    escaped = fstr_body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return 'f"""' + escaped + '"""'


def _state_expr_from_str(s: str) -> str:
    """Convert a possibly-interpolated string to a Python expression using STATE.get()."""
    single = re.compile(r"^\{\{\s*(\w+)\s*\}\}$")
    m = single.match(s.strip())
    if m:
        return f"STATE.get('{m.group(1)}', '')"
    inter = re.compile(r"\{\{\s*(\w+)\s*\}\}")
    if inter.search(s):
        body = inter.sub(lambda x: "{STATE.get('" + x.group(1) + "', '')}", s)
        escaped = body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return 'f"""' + escaped + '"""'
    return repr(s)


def _condition_to_expr(cond: Condition) -> str:
    """Translate a Condition tree to a Python boolean expression over STATE lookups."""
    if cond.op == "equals":
        lhs = _state_expr_from_str(cond.value or "")
        rhs = _state_expr_from_str(cond.text or "")
        return f"{lhs} == {rhs}"
    if cond.op == "contains":
        text_e = _state_expr_from_str(cond.text or "")
        val_e = _state_expr_from_str(cond.value or "")
        return f"{text_e} in {val_e}"
    if cond.op == "not":
        return f"not ({_condition_to_expr(cond.children[0])})"
    if cond.op == "and":
        parts = [f"({_condition_to_expr(c)})" for c in cond.children]
        return " and ".join(parts)
    if cond.op == "or":
        parts = [f"({_condition_to_expr(c)})" for c in cond.children]
        return " or ".join(parts)
    return "True"


def _condition_to_negated_expr(cond: Condition) -> str:
    """Translate a Condition tree to the *negation* of its boolean expression.

    Emitted directly (``!=``, ``not in``, De Morgan) rather than wrapping the
    positive form in ``not (...)`` so the generated loop-break stays ruff-clean
    (ruff rewrites ``not (a in b)`` to ``a not in b``).  Used for loop early-exit:
    ``if <guard fails>: break``.
    """
    if cond.op == "equals":
        lhs = _state_expr_from_str(cond.value or "")
        rhs = _state_expr_from_str(cond.text or "")
        return f"{lhs} != {rhs}"
    if cond.op == "contains":
        text_e = _state_expr_from_str(cond.text or "")
        val_e = _state_expr_from_str(cond.value or "")
        return f"{text_e} not in {val_e}"
    if cond.op == "not":
        # not(not X) == X
        return _condition_to_expr(cond.children[0])
    if cond.op == "and":
        # De Morgan: not(A and B) == (not A) or (not B)
        parts = [f"({_condition_to_negated_expr(c)})" for c in cond.children]
        return " or ".join(parts)
    if cond.op == "or":
        parts = [f"({_condition_to_negated_expr(c)})" for c in cond.children]
        return " and ".join(parts)
    return "False"


def _wrap_string_expr(expr: str, indent: int = 4) -> str:
    """Return expr; append ``# noqa: E501`` if the assignment line would exceed 120 chars."""
    prefix = " " * indent
    # Account for "    _sys = " (indent + 7 chars)
    if len(prefix) + 7 + len(expr) <= 120:
        return expr
    return expr + "  # noqa: E501"


def _script_is_async(node: NodeDef) -> bool:
    """Whether a script node's function is ``async``.

    Inline ``code`` is inspected via AST (the loader guarantees one top-level
    function).  A ``run`` ref is assumed async — the shipped ``run``-backed
    script helpers are all ``async def`` — matching the executor's ``await`` of
    any awaitable return.
    """
    if node.code is None:
        return True
    tree = ast.parse(node.code)
    return any(isinstance(s, ast.AsyncFunctionDef) for s in tree.body)


def _render_script_node(node: NodeDef, fn_name: str, output_key: str) -> str:
    """Render a script node's async wrapper: build ctx, pass typed inputs, store.

    Mirrors the executor's ``ctx``-first dispatch: the resolved function is
    called as ``fn(ctx, **typed_inputs)`` and its return value is coerced back to
    a state string via ``to_state`` (falling back to ``str`` when the output is
    untyped).  Both inline ``code`` and ``run`` refs resolve to a top-level
    ``_script_<id>`` callable (see :func:`_render_inline_scripts` /
    :func:`_render_script_imports`).
    """
    safe = _safe_id(node.id)
    type_of = dict(node.input_schema)
    lines = [
        f"async def {fn_name}(provider: object) -> None:",
        f'    _ctx = RuntimeContext(state=dict(STATE), workflow_name="{_ESC(node.id)}", node_id="{_ESC(node.id)}")',
    ]
    call_args = ["_ctx"]
    for name in node.inputs:
        jtype = type_of.get(name, "string")
        lines.append(f'    _in_{name} = to_python(STATE.get("{name}", ""), "{jtype}")')
        call_args.append(f"{name}=_in_{name}")
    await_kw = "await " if _script_is_async(node) else ""
    lines.append(f"    _val = {await_kw}_script_{safe}({', '.join(call_args)})")
    if node.output_type:
        lines.append(f'    STATE["{output_key}"] = to_state(_val, "{node.output_type}")')
    else:
        lines.append(f'    STATE["{output_key}"] = str(_val)')
    return "\n".join(lines)


def _rename_inline_fn(code: str, alias: str) -> str:
    """Return *code* with its single top-level function renamed to *alias*.

    The loader has already validated that inline ``code`` defines exactly one
    top-level function, so we rewrite that definition's name to a stable,
    collision-free ``_script_<id>`` symbol emitted at module top level.
    """
    tree = ast.parse(code)
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            stmt.name = alias
            break
    return ast.unparse(tree)


def _render_inline_scripts(wf: WorkflowDef) -> str:
    """Emit each inline-``code`` script node's function as a top-level ``_script_<id>``."""
    blocks: list[str] = []
    for node in wf.nodes:
        if node.type == "script" and node.code is not None:
            alias = f"_script_{_safe_id(node.id)}"
            blocks.append(_rename_inline_fn(node.code, alias))
    return "\n\n\n".join(blocks)


def _render_node_function(node_id: str, wf: WorkflowDef) -> str:
    node_map = {n.id: n for n in wf.nodes}
    node = node_map[node_id]
    fn_name = f"node_{_safe_id(node_id)}"
    output_key = node.output or node_id

    if node.type == "script":
        return _render_script_node(node, fn_name, output_key)

    model = node.model or wf.default_model
    sys_prompt = _render_prompt(node.system_prompt)
    user_prompt = _render_prompt(node.prompt)

    sys_lines = _wrap_string_expr(sys_prompt, indent=4)
    usr_lines = _wrap_string_expr(user_prompt, indent=4)
    lines = [
        f"async def {fn_name}(provider: object) -> None:",
        f"    _sys = {sys_lines}",
        f"    _usr = {usr_lines}",
    ]
    if node.tools:
        tool_names = ", ".join(f'"{t}"' for t in node.tools)
        lines.append(f"    _tools = _REGISTRY.resolve(({tool_names},))")
        lines.append(f'    result = await _run_agent(provider, "{model}", _sys, _usr, _tools)')
    else:
        lines.append(f'    result = await _run_agent(provider, "{model}", _sys, _usr)')
    lines.append(f'    STATE["{output_key}"] = result')
    return "\n".join(lines)


def _build_fwd_graph(
    wf: WorkflowDef,
) -> tuple[dict[str, list[EdgeDef]], dict[str, list[str]], list[EdgeDef]]:
    """Split edges into forward (non-loop) and loop back-edges.

    Returns:
        fwd_from: node_id -> list[EdgeDef] for forward edges
        fwd_preds: node_id -> list[predecessor node_ids] for forward edges
        loop_edges: all EdgeDef where loop_max is not None
    """
    fwd_from: dict[str, list[EdgeDef]] = defaultdict(list)
    fwd_preds: dict[str, list[str]] = defaultdict(list)
    loop_edges: list[EdgeDef] = []
    for e in wf.edges:
        if e.loop_max is None:
            fwd_from[e.src].append(e)
            fwd_preds[e.dst].append(e.src)
        else:
            loop_edges.append(e)
    return fwd_from, fwd_preds, loop_edges


def _node_guard(node_id: str, fwd_preds: dict[str, list[str]], fwd_from: dict[str, list[EdgeDef]]) -> str | None:
    """Return a Python guard expression if *node_id* is reached ONLY via conditional edges.

    A node is unconditionally reachable when at least one forward in-edge has no
    ``when`` (it always fires once the predecessor completes) — such a node needs
    no guard.  When every forward in-edge is conditional, the node runs iff any of
    those conditions holds, so we OR them together.  Returns ``None`` for an
    unconditional node (including the entry node, which has no in-edges).
    """
    in_edges = [e for p in fwd_preds.get(node_id, []) for e in fwd_from.get(p, []) if e.dst == node_id]
    if not in_edges:
        return None
    if any(e.when is None for e in in_edges):
        return None
    exprs = [f"({_condition_to_expr(e.when)})" for e in in_edges if e.when is not None]
    return " or ".join(exprs) if len(exprs) > 1 else exprs[0]


def _render_main_body(wf: WorkflowDef) -> str:
    """Generate the body of async def main() for the given workflow.

    Handles:
    - Sequential execution (single node per BFS wave)
    - Parallel fan-out/fan-in via asyncio.gather (multiple nodes per BFS wave)
    - Conditional edges via ``if <expr>:`` blocks
    - Bounded loops via ``for _loop_i in range(loop_max):``
    """
    fwd_from, fwd_preds, loop_edges = _build_fwd_graph(wf)

    # loop_entry_map: entry_node -> (exit_node, loop_max)
    # The loop body is all nodes between entry_node and exit_node (inclusive) in
    # the forward graph.  We emit a ``for`` block that opens at entry_node and
    # closes after exit_node.  loop_exit_break maps the exit_node to the back
    # edge's condition (if any), so the ``for`` can early-break as soon as the
    # loop guard fails — mirroring the executor, which re-fires a loop only while
    # its condition holds.  (Entry and exit land in different BFS waves, so this
    # must be keyed state, not a per-wave local.)
    loop_entry_map: dict[str, tuple[str, int | None]] = {}
    loop_exit_set: set[str] = set()
    loop_exit_break: dict[str, str | None] = {}
    for le in loop_edges:
        loop_entry_map[le.dst] = (le.src, le.loop_max)
        loop_exit_set.add(le.src)
        # Store the break expression (guard-fails) directly, so the emitted
        # ``if <expr>: break`` is ruff-clean without a wrapping ``not (...)``.
        loop_exit_break[le.src] = _condition_to_negated_expr(le.when) if le.when is not None else None

    completed: set[str] = set()
    pending: list[str] = [wf.entry]
    lines: list[str] = []
    loop_depth = 0

    def ind() -> str:
        return "    " * (1 + loop_depth)

    while pending:
        # All nodes whose forward predecessors are complete
        ready = [n for n in pending if all(p in completed for p in fwd_preds.get(n, []))]
        if not ready:
            break
        for n in ready:
            pending.remove(n)

        # Open a loop block if any ready node is a loop entry.
        for n in ready:
            if n in loop_entry_map:
                _, lmax = loop_entry_map[n]
                lines.append(f"{ind()}for _loop_i in range({lmax}):")
                loop_depth += 1
                break

        # Emit the wave.  Nodes reached unconditionally can run concurrently via
        # gather; nodes reached ONLY through conditional edges must each be
        # guarded by their own ``if`` so a branch that shouldn't fire doesn't.
        unconditional = [n for n in ready if _node_guard(n, fwd_preds, fwd_from) is None]
        conditional = [n for n in ready if _node_guard(n, fwd_preds, fwd_from) is not None]

        if len(unconditional) == 1 and not conditional:
            lines.append(f"{ind()}await node_{_safe_id(unconditional[0])}(provider)")
        elif unconditional:
            calls = [f"node_{_safe_id(n)}(provider)" for n in unconditional]
            if len(calls) == 1:
                lines.append(f"{ind()}await {calls[0]}")
            else:
                lines.append(f"{ind()}await asyncio.gather({', '.join(calls)})")
        for n in conditional:
            guard = _node_guard(n, fwd_preds, fwd_from)
            lines.append(f"{ind()}if {guard}:")
            lines.append(f"{ind()}    await node_{_safe_id(n)}(provider)")

        for n in ready:
            completed.add(n)

        # Close loop block when the loop exit node is processed, emitting the
        # early-break so a conditional loop stops as soon as its guard fails.
        for n in ready:
            if n in loop_exit_set:
                break_cond = loop_exit_break.get(n)
                if break_cond is not None:
                    lines.append(f"{ind()}if {break_cond}:")
                    lines.append(f"{ind()}    break")
                loop_depth -= 1
                break

        # Discover forward successors
        for n in ready:
            for e in fwd_from.get(n, []):
                succ = e.dst
                if succ not in completed and succ not in pending:
                    pending.append(succ)

    return "\n".join(lines)


def _render_script_imports(wf: WorkflowDef) -> str:
    """Emit top-level imports for flow.tools registry and any script node run functions.

    ``from flow.tools import default_registry`` is always emitted.  Script-node
    imports are appended; if they also come from ``flow.tools`` they are merged
    onto the same line to satisfy ruff isort.
    """
    # Collect script-node imports keyed by module
    extra: dict[str, list[str]] = {}  # module -> ["func as alias", ...]
    for node in wf.nodes:
        if node.type == "script" and node.run:
            module, func = node.run.rsplit(":", 1)
            safe = _safe_id(node.id)
            alias = f"{func} as _script_{safe}"
            extra.setdefault(module, []).append(alias)

    # Build the flow.tools import lines (always needed for default_registry).
    # Keep aliased imports on a separate line from non-aliased ones so that
    # ruff isort (combine-as-imports=false by default) does not reformat them.
    flow_tools_aliases = extra.pop("flow.tools", [])
    lines: list[str] = ["from flow.tools import default_registry"]

    # Script nodes use the executor's ctx-first typed dispatch.  Emit only the
    # coercers actually referenced by the generated code, so an unused import
    # never trips ruff: ``to_python`` appears iff some script node has inputs;
    # ``to_state`` iff some script node declares an output type; ``RuntimeContext``
    # whenever any script node exists (its wrapper always builds a ctx).
    script_nodes = [n for n in wf.nodes if n.type == "script"]
    if script_nodes:
        coercers = []
        if any(n.inputs for n in script_nodes):
            coercers.append("to_python")
        if any(n.output_type for n in script_nodes):
            coercers.append("to_state")
        if coercers:
            lines.insert(0, f"from flow.coerce import {', '.join(coercers)}")
        lines.insert(1 if coercers else 0, "from flow.runtime import RuntimeContext")

    for alias in flow_tools_aliases:
        lines.append(f"from flow.tools import {alias}")

    # Remaining non-flow.tools script imports
    for module, aliases in sorted(extra.items()):
        for alias in aliases:
            lines.append(f"from {module} import {alias}")

    return "\n".join(lines) + "\n"


def generate(wf: WorkflowDef) -> str:
    """Generate a complete Python module string for the given WorkflowDef."""
    tmpl_path = importlib.resources.files("flow") / "templates" / "runtime.py.tmpl"
    tmpl_text = tmpl_path.read_text(encoding="utf-8")

    # Inline-``code`` script functions are emitted at module top level (renamed to
    # ``_script_<id>``), ahead of the per-node async wrappers that call them.
    inline_scripts = _render_inline_scripts(wf)
    node_functions = "\n\n\n".join(_render_node_function(n.id, wf) for n in wf.nodes)
    if inline_scripts:
        node_functions = inline_scripts + "\n\n\n" + node_functions
    main_body = _render_main_body(wf)

    result = string.Template(tmpl_text).substitute(
        workflow_name=wf.name,
        initial_state=_render_initial_state(wf),
        node_functions=node_functions,
        provider=wf.provider,
        main_body=main_body,
        script_imports=_render_script_imports(wf),
    )
    return result
