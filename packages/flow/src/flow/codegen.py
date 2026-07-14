"""flow.codegen — generate a self-contained Python module from a WorkflowDef."""

from __future__ import annotations

import importlib.resources
import re
import string
from collections import defaultdict

from flow.models import Condition, EdgeDef, WorkflowDef


def _safe_id(node_id: str) -> str:
    """Convert a node id to a valid Python identifier."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", node_id)


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


def _wrap_string_expr(expr: str, indent: int = 4) -> str:
    """Return expr; append ``# noqa: E501`` if the assignment line would exceed 120 chars."""
    prefix = " " * indent
    # Account for "    _sys = " (indent + 7 chars)
    if len(prefix) + 7 + len(expr) <= 120:
        return expr
    return expr + "  # noqa: E501"


def _render_node_function(node_id: str, wf: WorkflowDef) -> str:
    node_map = {n.id: n for n in wf.nodes}
    node = node_map[node_id]
    fn_name = f"node_{_safe_id(node_id)}"
    output_key = node.output or node_id

    if node.type == "script":
        safe = _safe_id(node_id)
        lines = [
            f"async def {fn_name}(provider: object) -> None:",
            f'    STATE["{output_key}"] = await _script_{safe}(STATE)',
        ]
        return "\n".join(lines)

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
    # closes after exit_node.
    loop_entry_map: dict[str, tuple[str, int | None]] = {}
    loop_exit_set: set[str] = set()
    for le in loop_edges:
        loop_entry_map[le.dst] = (le.src, le.loop_max)
        loop_exit_set.add(le.src)

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

        # Open a loop block if any ready node is a loop entry
        for n in ready:
            if n in loop_entry_map:
                _, lmax = loop_entry_map[n]
                lines.append(f"{ind()}for _loop_i in range({lmax}):")
                loop_depth += 1
                break

        # Emit the wave
        if len(ready) == 1:
            n = ready[0]
            # Detect if this node is only reachable via a conditional edge
            cond_edges = [
                e for p in fwd_preds.get(n, []) for e in fwd_from.get(p, []) if e.dst == n and e.when is not None
            ]
            unconditional_preds = [
                e for p in fwd_preds.get(n, []) for e in fwd_from.get(p, []) if e.dst == n and e.when is None
            ]
            if cond_edges and not unconditional_preds:
                cond_expr = _condition_to_expr(cond_edges[0].when)  # type: ignore[arg-type]
                lines.append(f"{ind()}if {cond_expr}:")
                lines.append(f"{ind()}    await node_{_safe_id(n)}(provider)")
            else:
                lines.append(f"{ind()}await node_{_safe_id(n)}(provider)")
        else:
            calls = [f"node_{_safe_id(n)}(provider)" for n in ready]
            lines.append(f"{ind()}await asyncio.gather({', '.join(calls)})")

        for n in ready:
            completed.add(n)

        # Close loop block when the loop exit node is processed
        for n in ready:
            if n in loop_exit_set:
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

    node_functions = "\n\n\n".join(_render_node_function(n.id, wf) for n in wf.nodes)
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
