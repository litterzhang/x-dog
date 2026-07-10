"""flow.codegen — generate a self-contained Python module from a WorkflowDef."""

from __future__ import annotations

import importlib.resources
import re
import string
from collections import defaultdict

from flow.models import WorkflowDef


def _topo_linear(wf: WorkflowDef) -> list[str]:
    """Return node IDs in linear topological order (entry first)."""
    edges_from: dict[str, list[str]] = defaultdict(list)
    for edge in wf.edges:
        if edge.loop_max is None:
            edges_from[edge.src].append(edge.dst)

    order: list[str] = []
    visited: set[str] = set()

    def _visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        order.append(node_id)
        for dst in edges_from[node_id]:
            _visit(dst)

    _visit(wf.entry)
    return order


def _safe_id(node_id: str) -> str:
    """Convert a node id to a valid Python identifier."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", node_id)


def _render_initial_state(wf: WorkflowDef) -> str:
    pairs = ", ".join(f'"{k}": "{v}"' for k, v in wf.initial_state)
    return "{" + pairs + "}"


def _render_prompt(prompt: str) -> str:
    """Emit prompt with {{key}} replaced by STATE['key'] lookups as an f-string."""
    # Find all {{key}} placeholders
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")
    keys = pattern.findall(prompt)
    if not keys:
        return repr(prompt)
    # Build an f-string: replace {{key}} with {STATE['key']}
    fstr_body = pattern.sub(lambda m: "{STATE['" + m.group(1) + "']}", prompt)
    # Escape any existing curly braces that are NOT our replacements first:
    # We already substituted placeholders to valid f-string refs, just wrap.
    return 'f"' + fstr_body.replace('"', '\\"') + '"'


def _render_node_function(node_id: str, wf: WorkflowDef) -> str:
    node_map = {n.id: n for n in wf.nodes}
    node = node_map[node_id]
    fn_name = f"node_{_safe_id(node_id)}"
    model = node.model or wf.default_model
    sys_prompt = _render_prompt(node.system_prompt)
    user_prompt = _render_prompt(node.prompt)
    output_key = node.output or node_id

    lines = [
        f"async def {fn_name}(provider: object) -> None:",
        f'    result = await _run_agent(provider, "{model}", {sys_prompt}, {user_prompt})',
        f'    STATE["{output_key}"] = result',
    ]
    return "\n".join(lines)


def _render_main_body(order: list[str]) -> str:
    calls = [f"    await node_{_safe_id(nid)}(provider)" for nid in order]
    return "\n".join(calls)


def generate(wf: WorkflowDef) -> str:
    """Generate a complete Python module string for the given WorkflowDef."""
    tmpl_path = importlib.resources.files("flow") / "templates" / "runtime.py.tmpl"
    tmpl_text = tmpl_path.read_text(encoding="utf-8")

    order = _topo_linear(wf)
    node_functions = "\n\n\n".join(_render_node_function(nid, wf) for nid in order)
    main_body = _render_main_body(order)

    result = string.Template(tmpl_text).substitute(
        workflow_name=wf.name,
        initial_state=_render_initial_state(wf),
        node_functions=node_functions,
        provider=wf.provider,
        main_body=main_body,
    )
    return result
