"""flow.builder.actions — pure edit operations over a BuilderModel.

Every action takes a :class:`~flow.builder.model.BuilderModel` and returns a new
one (never mutates), rebuilding the frozen :class:`~flow.models.WorkflowDef` via
``dataclasses.replace``.  ``BuilderModel.with_wf`` re-runs validation after each
edit, so the builder always knows the current error state.  All of this is
terminal-free and unit-tested.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Literal

from flow.builder.model import BuilderModel
from flow.models import Condition, EdgeDef, NodeDef, Port, WorkflowDef

# Node scalar fields the editor form can set directly.
_STR_FIELDS = frozenset({"model", "system_prompt", "prompt", "run"})


def _unique_node_id(wf: WorkflowDef, base: str) -> str:
    existing = {n.id for n in wf.nodes}
    if base not in existing:
        return base
    i = 2
    while f"{base}{i}" in existing:
        i += 1
    return f"{base}{i}"


def add_node(model: BuilderModel, node_type: str = "agent") -> BuilderModel:
    """Append a new node of *node_type* ('agent' or 'script') and select it."""
    literal_type: Literal["agent", "script"] = "script" if node_type == "script" else "agent"
    node_id = _unique_node_id(model.wf, literal_type)
    node = NodeDef(id=node_id, type=literal_type)
    wf = replace(model.wf, nodes=model.wf.nodes + (node,))
    # First node becomes the entry point by default.
    if not model.wf.nodes:
        wf = replace(wf, entry=node_id)
    return replace(model.with_wf(wf), selected=node_id)


def remove_node(model: BuilderModel, node_id: str) -> BuilderModel:
    """Remove *node_id* and any edges touching it."""
    nodes = tuple(n for n in model.wf.nodes if n.id != node_id)
    edges = tuple(e for e in model.wf.edges if e.src != node_id and e.dst != node_id)
    entry = model.wf.entry
    if entry == node_id:
        entry = nodes[0].id if nodes else ""
    wf = replace(model.wf, nodes=nodes, edges=edges, entry=entry)
    return model.with_wf(wf)


def _map_node(wf: WorkflowDef, node_id: str, fn: Callable[[NodeDef], NodeDef]) -> WorkflowDef:
    nodes = tuple(fn(n) if n.id == node_id else n for n in wf.nodes)
    return replace(wf, nodes=nodes)


def rename_node(model: BuilderModel, node_id: str, new_id: str) -> BuilderModel:
    """Rename *node_id* to *new_id*, updating edges and entry that reference it."""
    if not new_id or new_id == node_id:
        return model
    nodes = tuple(replace(n, id=new_id) if n.id == node_id else n for n in model.wf.nodes)
    edges = tuple(
        replace(
            e,
            src=new_id if e.src == node_id else e.src,
            dst=new_id if e.dst == node_id else e.dst,
        )
        for e in model.wf.edges
    )
    entry = new_id if model.wf.entry == node_id else model.wf.entry
    wf = replace(model.wf, nodes=nodes, edges=edges, entry=entry)
    new_model = model.with_wf(wf)
    return replace(new_model, selected=new_id if model.selected == node_id else new_model.selected)


def set_field(model: BuilderModel, node_id: str, field_name: str, value: str) -> BuilderModel:
    """Set a scalar node field (model/system_prompt/prompt/run).

    Empty string clears the optional fields (model/run) to ``None``.
    """
    if field_name not in _STR_FIELDS:
        raise ValueError(f"unknown node field {field_name!r}")
    stored: str | None = value
    if field_name in ("model", "run") and value == "":
        stored = None

    def _apply(n: NodeDef) -> NodeDef:
        if field_name == "model":
            return replace(n, model=stored)
        if field_name == "system_prompt":
            return replace(n, system_prompt=value)
        if field_name == "prompt":
            return replace(n, prompt=value)
        return replace(n, run=stored)  # field_name == "run"

    return model.with_wf(_map_node(model.wf, node_id, _apply))


def set_tools(model: BuilderModel, node_id: str, tools: tuple[str, ...]) -> BuilderModel:
    """Replace a node's tool list."""
    wf = _map_node(model.wf, node_id, lambda n: replace(n, tools=tuple(tools)))
    return model.with_wf(wf)


def set_input_ports(model: BuilderModel, node_id: str, ports: tuple[Port, ...]) -> BuilderModel:
    """Replace a node's input ports."""
    wf = _map_node(model.wf, node_id, lambda n: replace(n, input_ports=tuple(ports)))
    return model.with_wf(wf)


def set_output_ports(model: BuilderModel, node_id: str, ports: tuple[Port, ...]) -> BuilderModel:
    """Replace a node's output ports."""
    wf = _map_node(model.wf, node_id, lambda n: replace(n, output_ports=tuple(ports)))
    return model.with_wf(wf)


def set_node_type(model: BuilderModel, node_id: str, node_type: str) -> BuilderModel:
    """Switch a node between 'agent' and 'script'."""
    if node_type not in ("agent", "script"):
        raise ValueError(f"invalid node type {node_type!r}")
    literal_type: Literal["agent", "script"] = "script" if node_type == "script" else "agent"
    wf = _map_node(model.wf, node_id, lambda n: replace(n, type=literal_type))
    return model.with_wf(wf)


def set_entry(model: BuilderModel, node_id: str) -> BuilderModel:
    """Set the workflow entry node."""
    return model.with_wf(replace(model.wf, entry=node_id))


def add_edge(
    model: BuilderModel,
    src: str,
    dst: str,
    *,
    when: Condition | None = None,
    loop_max: int | None = None,
) -> BuilderModel:
    """Add an edge src -> dst (optionally conditional and/or a bounded loop)."""
    edge = EdgeDef(src=src, dst=dst, when=when, loop_max=loop_max)
    wf = replace(model.wf, edges=model.wf.edges + (edge,))
    return model.with_wf(wf)


def remove_edge(model: BuilderModel, index: int) -> BuilderModel:
    """Remove the edge at position *index* (as listed in ``wf.edges``)."""
    if not (0 <= index < len(model.wf.edges)):
        return model
    edges = model.wf.edges[:index] + model.wf.edges[index + 1 :]
    return model.with_wf(replace(model.wf, edges=edges))


def set_provider(model: BuilderModel, provider: str) -> BuilderModel:
    """Set the workflow provider."""
    return model.with_wf(replace(model.wf, provider=provider))


def set_default_model(model: BuilderModel, default_model: str) -> BuilderModel:
    """Set the workflow default model."""
    return model.with_wf(replace(model.wf, default_model=default_model))


def set_initial_state(model: BuilderModel, state: tuple[tuple[str, str], ...]) -> BuilderModel:
    """Replace the workflow initial state."""
    return model.with_wf(replace(model.wf, initial_state=tuple(state)))


def select(model: BuilderModel, node_id: str | None) -> BuilderModel:
    """Change the selected node without touching the workflow."""
    return replace(model, selected=node_id)
