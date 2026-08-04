"""Pure frontier/token graph planning and state transitions.

The interpreter imports this module directly; codegen later embeds the same pure
transition functions into generated modules.  Conditions and node execution stay
host callbacks — this module only reasons about graph topology, activations, and
bounded-loop joins.
"""

from __future__ import annotations

import inspect

from flow.models import IN_NODE_ID, OUT_NODE_ID, WorkflowDef, edge_identities, entry_frontier

FrontierSpec = dict[str, object]
FrontierState = dict[str, object]
Activation = tuple[str, int, tuple[str, ...]]
Completion = tuple[str, int, dict[str, bool]]


def _dict(value: object) -> dict[object, object]:
    if not isinstance(value, dict):
        raise TypeError(f"frontier value must be a dict, got {type(value).__name__}")
    return value


def _set(value: object) -> set[object]:
    if not isinstance(value, set):
        raise TypeError(f"frontier value must be a set, got {type(value).__name__}")
    return value


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"frontier value must be a tuple, got {type(value).__name__}")
    return value


def _int(value: object) -> int:
    if not isinstance(value, int):
        raise TypeError(f"frontier value must be an int, got {type(value).__name__}")
    return value


def build_frontier_spec(wf: WorkflowDef) -> FrontierSpec:
    """Compile *wf* into stable, literal-safe scheduler metadata."""
    edge_ids = edge_identities(wf)
    nodes = tuple(node.id for node in wf.nodes)
    node_order = {node_id: index for index, node_id in enumerate(nodes)}
    edges: dict[str, object] = {}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    forward_predecessors: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    loop_groups: dict[str, list[str]] = {}
    forward_adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}

    for index, (edge, edge_id) in enumerate(zip(wf.edges, edge_ids, strict=True)):
        is_loop = edge.loop_max is not None
        edges[edge_id] = {
            "id": edge_id,
            "index": index,
            "src": edge.src,
            "dst": edge.dst,
            "loop": is_loop,
            "max": edge.loop_max,
            "strict": edge.loop_strict,
        }
        if edge.src in outgoing and edge.dst != OUT_NODE_ID:
            outgoing[edge.src].append(edge_id)
        if is_loop:
            if edge.dst != OUT_NODE_ID:
                loop_groups.setdefault(edge.dst, []).append(edge_id)
            continue
        if edge.dst == OUT_NODE_ID:
            continue
        if edge.src != IN_NODE_ID:
            predecessors = forward_predecessors[edge.dst]
            if edge.src not in predecessors:
                predecessors.append(edge.src)
            if edge.dst not in forward_adjacency[edge.src]:
                forward_adjacency[edge.src].append(edge.dst)

    invalidation_regions: dict[str, tuple[str, ...]] = {}
    for destination in loop_groups:
        visited = {destination}
        queue = [destination]
        while queue:
            current = queue.pop(0)
            for successor in forward_adjacency.get(current, []):
                if successor not in visited:
                    visited.add(successor)
                    queue.append(successor)
        invalidation_regions[destination] = tuple(sorted(visited, key=node_order.__getitem__))

    return {
        "nodes": nodes,
        "entries": entry_frontier(wf),
        "node_order": node_order,
        "edges": edges,
        "outgoing": {node: tuple(ids) for node, ids in outgoing.items()},
        "forward_predecessors": {
            node: tuple(predecessors) for node, predecessors in forward_predecessors.items()
        },
        "loop_groups": {destination: tuple(ids) for destination, ids in loop_groups.items()},
        "invalidation_regions": invalidation_regions,
    }


def new_frontier_state(
    spec: FrontierSpec,
    completed: set[str] | None = None,
) -> FrontierState:
    """Create transient scheduler state and seed the entry frontier."""
    nodes = tuple(str(node) for node in _tuple(spec["nodes"]))
    generations = {node: 0 for node in nodes}
    restored = completed or set()
    completed_generations = {node: 0 for node in restored if node in generations}
    reached = {
        (str(node), 0)
        for node in _tuple(spec["entries"])
        if str(node) not in completed_generations
    }
    return {
        "generations": generations,
        "reached": reached,
        "running": set(),
        "completed": completed_generations,
        "enabled": {},
        "isolated": set(),
        "isolated_nodes": set(),
        "loop_arrivals": {},
        "loop_closed": set(),
    }


def _generation(state: FrontierState, node_id: str) -> int:
    generations = _dict(state["generations"])
    return _int(generations[node_id])


def _ready(spec: FrontierSpec, state: FrontierState) -> list[Activation]:
    reached = _set(state["reached"])
    running = _set(state["running"])
    isolated = _set(state["isolated"])
    isolated_nodes = _set(state["isolated_nodes"])
    completed = _dict(state["completed"])
    predecessors_by_node = _dict(spec["forward_predecessors"])
    enabled = _dict(state["enabled"])
    ready: list[Activation] = []

    for raw_node in _tuple(spec["nodes"]):
        node_id = str(raw_node)
        epoch = _generation(state, node_id)
        activation = (node_id, epoch)
        if (
            activation not in reached
            or activation in running
            or activation in isolated
            or node_id in isolated_nodes
        ):
            continue
        if completed.get(node_id) == epoch:
            continue
        predecessors = _tuple(predecessors_by_node[node_id])
        if any(completed.get(str(predecessor)) != _generation(state, str(predecessor)) for predecessor in predecessors):
            continue
        raw_enabled = enabled.get(activation, ())
        edge_ids = tuple(str(edge_id) for edge_id in _tuple(raw_enabled))
        ready.append((node_id, epoch, edge_ids))
    return ready


def take_ready(spec: FrontierSpec, state: FrontierState) -> list[Activation]:
    """Lease the stable ready frontier in workflow node order."""
    ready = _ready(spec, state)
    running = _set(state["running"])
    for node_id, epoch, _enabled in ready:
        running.add((node_id, epoch))
    return ready


def is_quiescent(state: FrontierState) -> bool:
    """Whether no activation is currently leased.

    Reached-but-structurally-blocked nodes intentionally do not prevent clean graph
    completion; the host calls :func:`take_ready` before checking quiescence.
    """
    return not _set(state["running"])


def isolate_nodes(state: FrontierState, node_ids: set[str]) -> None:
    """Suppress current and future activations for the given static node ids."""
    isolated_nodes = _set(state["isolated_nodes"])
    isolated_nodes.update(node_ids)
    isolated = _set(state["isolated"])
    reached = _set(state["reached"])
    running = _set(state["running"])
    for node_id in node_ids:
        activation = (node_id, _generation(state, node_id))
        isolated.add(activation)
        reached.discard(activation)
        running.discard(activation)


def _enable_edge(spec: FrontierSpec, state: FrontierState, edge_id: str) -> None:
    edges = _dict(spec["edges"])
    edge = _dict(edges[edge_id])
    destination = str(edge["dst"])
    epoch = _generation(state, destination)
    activation = (destination, epoch)
    _set(state["reached"]).add(activation)
    enabled = _dict(state["enabled"])
    current = tuple(str(item) for item in _tuple(enabled.get(activation, ())))
    if edge_id not in current:
        enabled[activation] = (*current, edge_id)


def _clear_node_epoch_state(state: FrontierState, nodes: tuple[str, ...]) -> None:
    node_set = set(nodes)
    reached = _set(state["reached"])
    running = _set(state["running"])
    isolated = _set(state["isolated"])
    reached.difference_update(item for item in tuple(reached) if isinstance(item, tuple) and item[0] in node_set)
    running.difference_update(item for item in tuple(running) if isinstance(item, tuple) and item[0] in node_set)
    isolated.difference_update(item for item in tuple(isolated) if isinstance(item, tuple) and item[0] in node_set)

    completed = _dict(state["completed"])
    for node_id in nodes:
        completed.pop(node_id, None)

    enabled = _dict(state["enabled"])
    for activation in tuple(enabled):
        if isinstance(activation, tuple) and activation[0] in node_set:
            enabled.pop(activation, None)

    arrivals = _dict(state["loop_arrivals"])
    closed = _set(state["loop_closed"])
    for key in tuple(arrivals):
        if isinstance(key, tuple) and key[0] in node_set:
            arrivals.pop(key, None)
    closed.difference_update(key for key in tuple(closed) if isinstance(key, tuple) and key[0] in node_set)


def _evaluate_loop_groups(
    spec: FrontierSpec,
    state: FrontierState,
    loop_counts: dict[str, int],
) -> str | None:
    groups = _dict(spec["loop_groups"])
    arrivals = _dict(state["loop_arrivals"])
    closed = _set(state["loop_closed"])
    edges = _dict(spec["edges"])
    regions = _dict(spec["invalidation_regions"])

    for raw_destination, raw_members in groups.items():
        destination = str(raw_destination)
        generation = _generation(state, destination)
        group_key = (destination, generation)
        if group_key in closed:
            continue
        member_ids = tuple(str(member) for member in _tuple(raw_members))
        group_arrivals = _dict(arrivals.get(group_key, {}))
        if any(member not in group_arrivals for member in member_ids):
            continue

        if any(group_arrivals[member] is not True for member in member_ids):
            closed.add(group_key)
            continue

        exhausted_strict = [
            member
            for member in member_ids
            if bool(_dict(edges[member])["strict"])
            and loop_counts.get(member, 0) >= _int(_dict(edges[member])["max"])
        ]
        if exhausted_strict:
            return exhausted_strict[0]

        if any(loop_counts.get(member, 0) >= _int(_dict(edges[member])["max"]) for member in member_ids):
            closed.add(group_key)
            continue

        for member in member_ids:
            loop_counts[member] = loop_counts.get(member, 0) + 1

        region = tuple(str(node) for node in _tuple(regions[destination]))
        _clear_node_epoch_state(state, region)
        generations = _dict(state["generations"])
        for node_id in region:
            generations[node_id] = _int(generations[node_id]) + 1

        new_generation = _generation(state, destination)
        activation = (destination, new_generation)
        _set(state["reached"]).add(activation)
        _dict(state["enabled"])[activation] = member_ids
    return None


def restore_loop_activations(
    spec: FrontierSpec,
    state: FrontierState,
    loop_counts: dict[str, int],
) -> None:
    """Rebind committed loop inputs for an incomplete destination on self-resume."""
    groups = _dict(spec["loop_groups"])
    completed = _dict(state["completed"])
    reached = _set(state["reached"])
    enabled = _dict(state["enabled"])
    for raw_destination, raw_members in groups.items():
        destination = str(raw_destination)
        if destination in completed:
            continue
        members = tuple(str(member) for member in _tuple(raw_members))
        if not members or any(loop_counts.get(member, 0) <= 0 for member in members):
            continue
        activation = (destination, _generation(state, destination))
        reached.add(activation)
        enabled[activation] = members


def replay_completed(
    spec: FrontierSpec,
    state: FrontierState,
    node_id: str,
    edge_results: dict[str, bool],
) -> None:
    """Rebuild forward reachability and partial loop arrivals from a checkpoint.

    Replay never increments loop counters or fires a group; it only reconstructs
    transient decisions from saved source outputs. Already-fired activations are
    restored separately from committed counters by :func:`restore_loop_activations`.
    """
    completed = _dict(state["completed"])
    epoch = _generation(state, node_id)
    completed[node_id] = epoch
    outgoing = _dict(spec["outgoing"])
    edges = _dict(spec["edges"])
    arrivals = _dict(state["loop_arrivals"])
    for raw_edge_id in _tuple(outgoing[node_id]):
        edge_id = str(raw_edge_id)
        edge = _dict(edges[edge_id])
        enabled = edge_results.get(edge_id, False)
        if not bool(edge["loop"]):
            if enabled:
                _enable_edge(spec, state, edge_id)
            continue
        destination = str(edge["dst"])
        group_key = (destination, _generation(state, destination))
        group_arrivals = arrivals.setdefault(group_key, {})
        if not isinstance(group_arrivals, dict):
            raise TypeError("loop arrivals must be a dict")
        group_arrivals[edge_id] = enabled


def complete_batch(
    spec: FrontierSpec,
    state: FrontierState,
    completions: list[Completion],
    loop_counts: dict[str, int],
) -> str | None:
    """Commit successful completions and return an exhausted strict edge id, if any."""
    running = _set(state["running"])
    completed = _dict(state["completed"])
    outgoing = _dict(spec["outgoing"])
    edges = _dict(spec["edges"])
    arrivals = _dict(state["loop_arrivals"])

    valid: list[Completion] = []
    for node_id, epoch, edge_results in completions:
        running.discard((node_id, epoch))
        if epoch != _generation(state, node_id):
            continue
        completed[node_id] = epoch
        valid.append((node_id, epoch, edge_results))

    # Resolve all forward transitions before evaluating loop joins. This keeps a
    # whole concurrent batch atomic from the graph scheduler's perspective.
    for node_id, _epoch, edge_results in valid:
        for raw_edge_id in _tuple(outgoing[node_id]):
            edge_id = str(raw_edge_id)
            edge = _dict(edges[edge_id])
            if bool(edge["loop"]):
                continue
            if edge_results.get(edge_id, False):
                _enable_edge(spec, state, edge_id)

    for node_id, epoch, edge_results in valid:
        for raw_edge_id in _tuple(outgoing[node_id]):
            edge_id = str(raw_edge_id)
            edge = _dict(edges[edge_id])
            if not bool(edge["loop"]):
                continue
            destination = str(edge["dst"])
            if epoch != _generation(state, node_id):
                continue
            group_key = (destination, _generation(state, destination))
            group_arrivals = arrivals.setdefault(group_key, {})
            if not isinstance(group_arrivals, dict):
                raise TypeError("loop arrivals must be a dict")
            group_arrivals[edge_id] = edge_results.get(edge_id, False)

    return _evaluate_loop_groups(spec, state, loop_counts)


_INLINE_FUNCTIONS = (
    _dict,
    _set,
    _tuple,
    _int,
    new_frontier_state,
    _generation,
    _ready,
    take_ready,
    is_quiescent,
    isolate_nodes,
    _enable_edge,
    _clear_node_epoch_state,
    _evaluate_loop_groups,
    restore_loop_activations,
    replay_completed,
    complete_batch,
)


def render_frontier_runtime() -> str:
    """Return the exact pure transition kernel for standalone generated modules."""
    aliases = (
        "FrontierSpec = dict[str, object]\n"
        "FrontierState = dict[str, object]\n"
        "Activation = tuple[str, int, tuple[str, ...]]\n"
        "Completion = tuple[str, int, dict[str, bool]]\n"
    )
    return aliases + "\n\n".join(inspect.getsource(function) for function in _INLINE_FUNCTIONS)
