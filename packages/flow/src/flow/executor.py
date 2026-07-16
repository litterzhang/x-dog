"""flow.executor — parallel (readiness-based) workflow executor."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import json
import logging
import sys
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.agent import Agent
from agent.core import AgentConfig, AgentTool, StreamFn
from agent.events import TurnEndEvent
from ai.types import AssistantMessage, TextContent

from flow.coerce import to_python, to_state
from flow.conditions import evaluate
from flow.errors import WorkflowExecutionError
from flow.interpolate import interpolate
from flow.models import EdgeDef, NodeDef, WorkflowDef
from flow.runtime import RuntimeContext
from flow.tools import ToolRegistry

logger = logging.getLogger(__name__)

ScriptResolver = Callable[[str], Callable[..., Awaitable[str]]]
ScriptFn = Callable[..., Any]


def _default_script_resolver(run: str) -> Callable[..., Awaitable[str]]:
    """Parse 'module:func' and import the callable."""
    module_name, _, func_name = run.partition(":")
    module = importlib.import_module(module_name)
    fn: Callable[..., Awaitable[str]] = getattr(module, func_name)
    return fn


def _resolve_script_fn(node: NodeDef, base_dir: Path | None) -> ScriptFn:
    """Resolve a script node's callable from inline ``code`` or a ``run`` ref.

    Inline code is ``exec``'d and the single top-level function returned.  A
    ``run`` ref is imported with *base_dir* (the workflow's own directory)
    temporarily on ``sys.path`` so a workflow bundles its sibling ``.py`` files.
    """
    if node.code is not None:
        namespace: dict[str, Any] = {}
        exec(node.code, namespace)  # noqa: S102 - inline workflow code, trusted author
        funcs = [v for v in namespace.values() if inspect.isfunction(v)]
        if len(funcs) != 1:
            raise WorkflowExecutionError(f"Script node {node.id!r}: code must define one function")
        return funcs[0]
    if node.run is None:
        raise WorkflowExecutionError(f"Script node {node.id!r}: no code or run")
    module_name, _, func_name = node.run.partition(":")
    if base_dir is not None:
        sys.path.insert(0, str(base_dir))
        importlib.invalidate_caches()
        try:
            module = importlib.import_module(module_name)
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(base_dir))
    else:
        module = importlib.import_module(module_name)
    fn: ScriptFn = getattr(module, func_name)
    return fn


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
    tool_registry: ToolRegistry | None = None,
    script_resolver: ScriptResolver | None = None,
    base_dir: Path | None = None,
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
    tool_registry:
        Registry of :class:`~agent.core.AgentTool` objects available to agent nodes.
        Defaults to :func:`~flow.tools.default_registry`.
    script_resolver:
        Callable that maps a ``'module:func'`` string to an async callable for script nodes.
        Defaults to the built-in importlib-based resolver.
    """
    from flow.tools import default_registry as _default_registry

    if tool_registry is None:
        tool_registry = _default_registry()
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

        if node.type == "script":
            if node.code is not None:
                fn = _resolve_script_fn(node, base_dir)
            elif script_resolver is not None:
                fn = script_resolver(node.run or "")
            else:
                fn = _resolve_script_fn(node, base_dir)

            async def _run_script(fn: ScriptFn = fn) -> None:
                ctx = RuntimeContext(
                    state=dict(state_snapshot),
                    workflow_name=wf.name,
                    node_id=node_id,
                )
                sig_params = list(inspect.signature(fn).parameters.values())
                first = sig_params[0].name if sig_params else ""
                if first == "ctx":
                    # New convention: fn(ctx, **typed-inputs-by-name).
                    type_of = dict(node.input_schema)
                    kwargs = {
                        name: to_python(state_snapshot.get(name, ""), type_of.get(name, "string"))
                        for name in node.inputs
                    }
                    raw = fn(ctx, **kwargs)
                else:
                    # Legacy convention: fn(state_dict).
                    raw = fn(dict(state_snapshot))
                value = await raw if inspect.isawaitable(raw) else raw
                if node.output is not None:
                    stored = to_state(value, node.output_type) if node.output_type else str(value)
                    async with _state_lock:
                        state[node.output] = stored
                        node_outputs[node.output] = stored

            await asyncio.wait_for(_run_script(), timeout=timeout)
            completed.add(node_id)
            return

        # agent node
        sys_prompt = interpolate(node.system_prompt, state_snapshot)
        user_prompt = interpolate(node.prompt, state_snapshot)

        model = node.model or wf.default_model
        # stream_fn_factory is guaranteed non-None here
        assert stream_fn_factory is not None
        stream_fn = stream_fn_factory(model)

        assert tool_registry is not None
        resolved_tools: tuple[AgentTool, ...] = tool_registry.resolve(node.tools)

        tool_ctx: dict[str, object] | None = None
        sink: dict[str, object] = {}
        final_sys_prompt = sys_prompt

        if node.output_schema:
            from agent.tools import create_submit_result_tool

            submit_tool = create_submit_result_tool()
            resolved_tools = resolved_tools + (submit_tool,)
            tool_ctx = {
                "flow_output_schema": dict(node.output_schema),
                "flow_result_sink": sink,
            }
            field_names = ", ".join(f for f, _ in node.output_schema)
            directive = (
                f"\nWhen finished, you MUST call the submit_result tool"
                f" with an object containing these fields: {field_names}."
            )
            final_sys_prompt = sys_prompt + directive

        agent = Agent(
            stream_fn,
            config=AgentConfig(model=model, system_prompt=final_sys_prompt),
            tools=resolved_tools,
            tool_ctx=tool_ctx,
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

        if node.output_schema:
            result_obj = sink.get("result")
            if result_obj is None:
                raise WorkflowExecutionError(f"Node {node.id!r}: agent did not submit a result via submit_result")
            output_text = json.dumps(result_obj, ensure_ascii=False, sort_keys=True)
        else:
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
