"""flow.executor — parallel (readiness-based) workflow executor."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from agent.agent import Agent
from agent.core import AgentConfig, StreamFn
from agent.events import TurnEndEvent
from ai.types import AssistantMessage, TextContent

from flow.conditions import evaluate
from flow.interpolate import interpolate
from flow.models import EdgeDef, WorkflowDef

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecResult:
    """Result of executing a workflow."""

    final_state: dict[str, str]
    node_outputs: dict[str, str]


async def execute(
    wf: WorkflowDef,
    *,
    stream_fn_factory: Callable[[str], StreamFn] | None = None,
    timeout: float = 120.0,
) -> ExecResult:
    """Execute a workflow definition with parallel fan-out/fan-in.

    A node is *ready* when all its non-loop predecessors have completed.
    All currently-ready nodes are launched concurrently via ``asyncio.gather``.
    A fan-in node waits until every upstream has finished.

    Linear graphs (no fan-out) behave identically to the previous implementation.

    Parameters
    ----------
    wf:
        The workflow definition to execute.
    stream_fn_factory:
        Optional factory ``(model_id) -> StreamFn``.  When provided it is
        used instead of the real ai provider — useful for injecting fakes
        in tests.
    timeout:
        Per-node wall-clock timeout in seconds.
    """
    if stream_fn_factory is None:
        import ai
        from agent.helpers import stream_fn_from_provider

        provider = ai.provider(wf.provider)
        default_stream_fn = stream_fn_from_provider(provider)

        def _factory(model: str) -> StreamFn:
            return default_stream_fn

        stream_fn_factory = _factory

    # Shared mutable state — only modified while holding _state_lock or before
    # any concurrency starts / after gather() returns.
    state: dict[str, str] = dict(wf.initial_state)
    node_outputs: dict[str, str] = {}
    _state_lock = asyncio.Lock()

    node_map = {n.id: n for n in wf.nodes}

    # edges_from: src -> list[EdgeDef]
    edges_from: dict[str, list[EdgeDef]] = defaultdict(list)
    # in_edges: dst -> list[EdgeDef]
    in_edges: dict[str, list[EdgeDef]] = defaultdict(list)
    for edge in wf.edges:
        edges_from[edge.src].append(edge)
        in_edges[edge.dst].append(edge)

    # Track which nodes have finished
    completed: set[str] = set()
    # Per-edge loop fire counter (loop_max edges only)
    loop_counters: dict[EdgeDef, int] = {}

    async def _run_node(node_id: str) -> None:
        """Execute a single node and update shared state."""
        node = node_map[node_id]

        async with _state_lock:
            state_snapshot = dict(state)

        sys_prompt = interpolate(node.system_prompt, state_snapshot)
        user_prompt = interpolate(node.prompt, state_snapshot)

        model = node.model or wf.default_model
        # stream_fn_factory is guaranteed non-None here
        assert stream_fn_factory is not None
        stream_fn = stream_fn_factory(model)

        agent = Agent(
            stream_fn,
            config=AgentConfig(model=model, system_prompt=sys_prompt),
        )

        logger.debug("Running node %r with model %r", node_id, model)

        accumulated: list[str] = []

        async def _drain() -> None:
            event_stream = await agent.prompt(user_prompt)
            async for event in event_stream:
                if isinstance(event, TurnEndEvent) and event.message is not None:
                    msg = event.message
                    if isinstance(msg, AssistantMessage):
                        for part in msg.content:
                            if isinstance(part, TextContent):
                                accumulated.append(part.text)

        await asyncio.wait_for(_drain(), timeout=timeout)

        output_text = "".join(accumulated)

        if node.output is not None:
            async with _state_lock:
                state[node.output] = output_text
                node_outputs[node.output] = output_text

        completed.add(node_id)

    def _is_ready(node_id: str) -> bool:
        """Return True if all non-loop predecessors of node_id have completed."""
        for edge in in_edges.get(node_id, []):
            if edge.loop_max is not None:
                continue  # skip loop back-edges
            if edge.src not in completed:
                return False
        return True

    def _successors(node_id: str) -> list[str]:
        """Return successor node IDs reachable from node_id given current state.

        Loop back-edges are handled separately by _activate_loops.
        """
        result: list[str] = []
        for edge in edges_from.get(node_id, []):
            if edge.loop_max is not None:
                continue  # loop edges handled separately
            if edge.when is None or evaluate(edge.when, state):
                result.append(edge.dst)
        return result

    def _transitive_successors(node_id: str) -> set[str]:
        """Return all nodes reachable from node_id (forward edges only, not loop edges)."""
        visited: set[str] = set()
        queue = [node_id]
        while queue:
            cur = queue.pop()
            for edge in edges_from.get(cur, []):
                if edge.loop_max is not None:
                    continue
                if edge.dst not in visited:
                    visited.add(edge.dst)
                    queue.append(edge.dst)
        return visited

    def _activate_loops(just_finished: list[str], pending: set[str]) -> None:
        """Check loop back-edges from just_finished nodes; re-activate dst if condition holds."""
        for node_id in just_finished:
            for edge in edges_from.get(node_id, []):
                if edge.loop_max is None:
                    continue
                count = loop_counters.get(edge, 0)
                if count >= edge.loop_max:
                    continue
                if edge.when is not None and not evaluate(edge.when, state):
                    continue
                # Fire the loop: increment counter, un-complete dst + its successors
                loop_counters[edge] = count + 1
                dst = edge.dst
                to_reset = {dst} | _transitive_successors(dst)
                for n in to_reset:
                    completed.discard(n)
                pending.add(dst)
                logger.debug("Loop edge %r->%r fired (count %d/%d)", node_id, dst, loop_counters[edge], edge.loop_max)

    # --- Scheduler loop ---
    # pending: nodes whose predecessors are all done but haven't started yet.
    # We seed with the entry node.
    pending: set[str] = {wf.entry}
    in_flight: set[str] = set()

    while pending or in_flight:
        # Collect all nodes that are ready right now
        ready = [n for n in pending if _is_ready(n)]
        if not ready:
            # Nothing is ready yet but something is in-flight — shouldn't
            # happen in a DAG without cycles, but guard against it.
            break

        for n in ready:
            pending.discard(n)
            in_flight.add(n)

        # Run all ready nodes concurrently
        await asyncio.gather(*[_run_node(n) for n in ready])

        for n in ready:
            in_flight.discard(n)

        # Check loop back-edges before discovering normal successors
        _activate_loops(ready, pending)

        # Discover newly unblocked nodes
        for n in ready:
            for succ in _successors(n):
                if succ not in completed and succ not in in_flight:
                    pending.add(succ)

    return ExecResult(final_state=state, node_outputs=node_outputs)
