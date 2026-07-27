"""flow.executor — parallel (readiness-based) workflow executor."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import json
import logging
import sys
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.agent import Agent
from agent.core import AgentConfig, AgentTool, StreamFn
from agent.events import TurnEndEvent
from ai.types import AssistantMessage, TextContent

from flow.checkpoint import CheckpointStore
from flow.coerce import to_python, to_state
from flow.conditions import evaluate
from flow.errors import WorkflowExecutionError
from flow.events import EventCallback, FlowEvent, NodeFailed, NodeFinished, NodeStarted
from flow.interpolate import interpolate
from flow.models import IN_NODE_ID, OUT_NODE_ID, EdgeDef, NodeDef, WorkflowDef
from flow.runtime import RuntimeContext
from flow.tools import ToolRegistry

logger = logging.getLogger(__name__)

ScriptResolver = Callable[[str], Callable[..., Awaitable[str]]]
ScriptFn = Callable[..., Any]


async def _wrap_sync(value: Any) -> Any:
    """Wrap a plain (non-awaitable) value in a coroutine for use with asyncio.wait_for."""
    return value


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
    """Result of executing a workflow.

    ``runtime`` is a single container describing the whole run:

    - ``ctx``   — runtime info of the last node to run: ``{step, node_id, workflow_name}``.
    - ``stack`` — per-node execution trace; one delta frame ``{step, node, in, out}``
      pushed each time a node runs (a looped node appears multiple times).
    - ``state`` — accumulated real-node outputs only: ``{node_id: {port: value}}``
      (excludes ``$in`` and ``$output``).
    - ``in``    — the workflow's initial values (the reserved ``$in`` source).
    - ``out``   — the collected workflow outputs (edges targeting ``$output``).
    """

    runtime: dict[str, Any]


async def execute(
    wf: WorkflowDef,
    *,
    stream_fn_factory: Callable[[str], StreamFn] | None = None,
    timeout: float = 120.0,
    tool_registry: ToolRegistry | None = None,
    script_resolver: ScriptResolver | None = None,
    base_dir: Path | None = None,
    inputs: Mapping[str, str] | None = None,
    web_search_fn_factory: Callable[[str], Callable[[str], Awaitable[str]]] | None = None,
    checkpoint: CheckpointStore | None = None,
    run_id: str | None = None,
    on_event: EventCallback | None = None,
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
    inputs:
        Optional run-time values for the ``$in`` source's output ports.  These
        override the workflow's ``state`` defaults key-by-key (a key absent from
        ``state`` simply seeds a new port), so the same workflow can run with
        different inputs without editing the JSON.
    web_search_fn_factory:
        Optional factory ``(model_id) -> async (query) -> str`` used to back the
        built-in ``web_search`` tool for agent nodes that set ``web_search``.
        Defaults to one built from the real ai provider.  Injectable for tests.
    """
    from flow.tools import default_registry as _default_registry
    from flow.tools import register_workflow_tools

    if tool_registry is None:
        tool_registry = _default_registry()
    if wf.tool_refs:
        register_workflow_tools(wf, tool_registry, base_dir)
    if stream_fn_factory is None:
        import ai
        from agent.helpers import stream_fn_from_provider

        provider = ai.provider(wf.provider)
        default_stream_fn = stream_fn_from_provider(provider)

        def _factory(model: str) -> StreamFn:
            return default_stream_fn

        stream_fn_factory = _factory

        if web_search_fn_factory is None:
            from agent.helpers import web_search_fn_from_provider

            def _ws_factory(model: str) -> Callable[[str], Awaitable[str]]:
                fn: Callable[[str], Awaitable[str]] = web_search_fn_from_provider(provider, model)
                return fn

            web_search_fn_factory = _ws_factory

    # Shared mutable output store: outputs[node_id][port] = value.  Seeded with
    # the reserved $in source carrying the workflow's initial values; run-time
    # ``inputs`` override those defaults key-by-key.  Only modified while holding
    # _state_lock or before/after concurrency.
    seed = dict(wf.initial_state)
    if inputs:
        seed.update(inputs)
    outputs: dict[str, dict[str, str]] = {IN_NODE_ID: seed}
    _state_lock = asyncio.Lock()

    # Per-node execution trace (delta frames) and a monotonic step counter.  Both
    # are only touched while holding _state_lock (as each node records its frame).
    stack: list[dict[str, Any]] = []
    step_counter = 0
    # Live workflow output: updated the moment a node feeding ``$output`` finishes,
    # so a looped writer's latest value wins and the result is progressive.
    out_live: dict[str, str] = {}

    node_map = {n.id: n for n in wf.nodes}

    # edges_from: src -> list[EdgeDef]
    edges_from: dict[str, list[EdgeDef]] = defaultdict(list)
    # in_edges: dst -> list[EdgeDef]
    in_edges: dict[str, list[EdgeDef]] = defaultdict(list)
    for edge in wf.edges:
        edges_from[edge.src].append(edge)
        in_edges[edge.dst].append(edge)

    def _source_ports(node_id: str) -> dict[str, str]:
        """Output ports available for reading from *node_id* (empty if not yet run)."""
        return outputs.get(node_id, {})

    def _build_inputs(node_id: str) -> dict[str, str]:
        """Assemble a node's input namespace from its incoming edge mappings.

        Walks in edge-declaration order; a later edge writing the same input port
        wins (only conditional/loop edges may legitimately share a port).  Loop
        back-edges only supply data while their condition holds.
        """
        ins: dict[str, str] = {}
        for edge in in_edges.get(node_id, []):
            if edge.loop_max is not None and edge.when is not None and not evaluate(edge.when, _source_ports(edge.src)):
                continue
            src_ports = _source_ports(edge.src)
            for sport, dport in edge.mapping:
                if sport in src_ports:
                    ins[dport] = src_ports[sport]
        return ins

    # Track which nodes have finished
    completed: set[str] = set()
    # Per-edge loop fire counter (loop_max edges only)
    loop_counters: dict[EdgeDef, int] = {}

    # -----------------------------------------------------------------------
    # Checkpoint restore — only when both checkpoint store and run_id given.
    # -----------------------------------------------------------------------
    _ckpt_active = checkpoint is not None and run_id is not None

    if _ckpt_active:
        assert checkpoint is not None and run_id is not None  # narrow types
        snap = checkpoint.load(run_id)
        if snap is not None:
            outputs = snap["outputs"]
            completed = set(snap["completed"])
            # Rebuild EdgeDef→int from "src->dst" string keys
            lc_raw: dict[str, int] = snap.get("loop_counters", {})
            for edge in wf.edges:
                key = f"{edge.src}->{edge.dst}"
                if key in lc_raw:
                    loop_counters[edge] = lc_raw[key]
            stack = snap.get("stack", [])
            out_live = snap.get("out_live", {})
            step_counter = len(stack)

    def _save_checkpoint() -> None:
        """Persist a snapshot of the current run state (called inside _state_lock)."""
        if not _ckpt_active:
            return
        assert checkpoint is not None and run_id is not None
        lc_serial = {f"{e.src}->{e.dst}": cnt for e, cnt in loop_counters.items()}
        snap: dict[str, Any] = {
            "outputs": outputs,
            "completed": list(completed),
            "loop_counters": lc_serial,
            "stack": stack,
            "out_live": out_live,
        }
        checkpoint.save(run_id, snap)

    async def _reserve_step() -> int:
        """Reserve the next monotonic step number (under the state lock)."""
        nonlocal step_counter
        async with _state_lock:
            step = step_counter
            step_counter += 1
        return step

    async def _record_frame(node_id: str, ins: dict[str, str], step: int) -> None:
        """Append a delta trace frame for a just-run node, and flush its outputs.

        Records the node's step, assembled input namespace, and its output ports.
        A looped node produces one frame per iteration, so the trace shows the
        refinement history.  If the node feeds ``$output``, its mapped keys are
        written to the live workflow output immediately (a looped writer's latest
        value wins).  Must be called after the node's outputs are stored.
        """
        async with _state_lock:
            node_ports = dict(outputs.get(node_id, {}))
            stack.append({"step": step, "node": node_id, "in": dict(ins), "out": node_ports})
            for edge in edges_from.get(node_id, []):
                if edge.dst != OUT_NODE_ID:
                    continue
                if edge.when is not None and not evaluate(edge.when, node_ports):
                    continue
                for sport, okey in edge.mapping:
                    if sport in node_ports:
                        out_live[okey] = node_ports[sport]

    def _emit(ev: FlowEvent) -> None:
        """Deliver a lifecycle event to the caller's callback (best-effort)."""
        if on_event is None:
            return
        try:
            on_event(ev)
        except Exception:
            pass

    async def _run_node(node_id: str) -> None:
        """Execute a single node and store its output ports."""
        # Skip nodes already completed (resume from checkpoint).
        if node_id in completed:
            return
        node = node_map[node_id]

        async with _state_lock:
            ins = _build_inputs(node_id)

        if node.type == "script":
            logger.debug("Running script node %r", node_id)
            if node.code is not None:
                fn = _resolve_script_fn(node, base_dir)
            elif script_resolver is not None:
                fn = script_resolver(node.run or "")
            else:
                fn = _resolve_script_fn(node, base_dir)

            step = await _reserve_step()
            _t0 = time.monotonic()
            _emit(NodeStarted(node_id=node_id, step=step))

            max_attempts = 1 + (node.retry.max if node.retry else 0)
            backoff = node.retry.backoff if node.retry else 0.0
            last_exc: BaseException | None = None
            for attempt in range(max_attempts):
                try:
                    ctx = RuntimeContext(step=step, node_id=node_id, workflow_name=wf.name)
                    kwargs = {p.name: to_python(ins.get(p.name, ""), p.type) for p in node.input_ports}
                    raw = fn(ctx, **kwargs)
                    value = await asyncio.wait_for(
                        raw if inspect.isawaitable(raw) else _wrap_sync(raw), timeout=timeout
                    )
                    await _store_script_output(node, node_id, value)
                    last_exc = None
                    break
                except BaseException as exc:
                    last_exc = exc
                    remaining = max_attempts - attempt - 1
                    if remaining > 0:
                        logger.debug(
                            "Retrying node %r (attempt %d/%d) after %s",
                            node_id,
                            attempt + 1,
                            max_attempts,
                            backoff * (attempt + 1),
                        )
                        await asyncio.sleep(backoff * (attempt + 1))
            if last_exc is not None:
                _emit(NodeFailed(
                    node_id=node_id, step=step,
                    duration_s=time.monotonic() - _t0,
                    error=f"{type(last_exc).__name__}: {last_exc}",
                ))
                raise last_exc
            await _record_frame(node_id, ins, step)
            completed.add(node_id)
            _save_checkpoint()
            _emit(NodeFinished(node_id=node_id, step=step, duration_s=time.monotonic() - _t0, tokens=0))
            return

        # agent node — prompt interpolation is PORT-LOCAL (reads this node's inputs)
        sys_prompt = interpolate(node.system_prompt, ins)
        user_prompt = interpolate(node.prompt, ins)

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

        # Enable the built-in web_search tool when the node opts in.  The search
        # model may differ from the node model (some models don't browse).
        web_search_fn = None
        if node.web_search and web_search_fn_factory is not None:
            web_search_fn = web_search_fn_factory(node.web_search_model or model)

        agent = Agent(
            stream_fn,
            config=AgentConfig(model=model, system_prompt=final_sys_prompt),
            tools=resolved_tools,
            tool_ctx=tool_ctx,
            web_search_fn=web_search_fn,
        )

        logger.debug("Running node %r with model %r", node_id, model)

        agent_max_attempts = 1 + (node.retry.max if node.retry else 0)
        agent_backoff = node.retry.backoff if node.retry else 0.0
        agent_last_exc: BaseException | None = None
        accumulated: list[str] = []
        agent_step = await _reserve_step()
        _t0_agent = time.monotonic()
        _emit(NodeStarted(node_id=node_id, step=agent_step))
        total_tokens = 0

        for agent_attempt in range(agent_max_attempts):
            accumulated = []
            sink.clear()
            total_tokens = 0

            async def _drain() -> None:
                nonlocal total_tokens
                event_stream = await agent.prompt(user_prompt)
                async for event in event_stream:
                    if isinstance(event, TurnEndEvent) and event.message is not None:
                        msg = event.message
                        if isinstance(msg, AssistantMessage):
                            for part in msg.content:
                                if isinstance(part, TextContent):
                                    accumulated.append(part.text)
                            total_tokens += msg.usage.total_tokens

            try:
                await asyncio.wait_for(_drain(), timeout=timeout)
                agent_last_exc = None
                break
            except BaseException as exc:
                agent_last_exc = exc
                remaining = agent_max_attempts - agent_attempt - 1
                if remaining > 0:
                    logger.debug(
                        "Retrying node %r (attempt %d/%d) after %s",
                        node_id,
                        agent_attempt + 1,
                        agent_max_attempts,
                        agent_backoff * (agent_attempt + 1),
                    )
                    await asyncio.sleep(agent_backoff * (agent_attempt + 1))

        if agent_last_exc is not None:
            _emit(NodeFailed(
                node_id=node_id, step=agent_step,
                duration_s=time.monotonic() - _t0_agent,
                error=f"{type(agent_last_exc).__name__}: {agent_last_exc}",
            ))
            raise agent_last_exc

        if node.output_schema:
            result_obj = sink.get("result")
            if result_obj is None:
                raise WorkflowExecutionError(f"Node {node.id!r}: agent did not submit a result via submit_result")
            output_text = json.dumps(result_obj, ensure_ascii=False, sort_keys=True)
        else:
            output_text = "".join(accumulated)

        # Agent nodes write their single output port (if declared).
        if node.output_ports:
            async with _state_lock:
                outputs.setdefault(node_id, {})[node.output_ports[0].name] = output_text

        await _record_frame(node_id, ins, agent_step)
        completed.add(node_id)
        _save_checkpoint()
        _emit(NodeFinished(
            node_id=node_id, step=agent_step,
            duration_s=time.monotonic() - _t0_agent,
            tokens=total_tokens,
        ))

    async def _store_script_output(node: NodeDef, node_id: str, value: Any) -> None:
        """Coerce a script's return value into the node's output port(s)."""
        ports = node.output_ports
        stored: dict[str, str]
        if len(ports) <= 1:
            if not ports:
                return
            p = ports[0]
            stored = {p.name: to_state(value, p.type)}
        else:
            # Multi-output script: the function must return a dict keyed by port name.
            if not isinstance(value, dict):
                raise WorkflowExecutionError(
                    f"Script node {node_id!r}: declares {len(ports)} output ports so must return a dict"
                )
            stored = {p.name: to_state(value[p.name], p.type) for p in ports}
        async with _state_lock:
            outputs.setdefault(node_id, {}).update(stored)

    def _is_ready(node_id: str) -> bool:
        """Return True if all non-loop predecessors of node_id have completed.

        The reserved ``$in`` source is always considered complete (it is pre-seeded
        with the workflow's initial values and never executes).
        """
        for edge in in_edges.get(node_id, []):
            if edge.loop_max is not None:
                continue  # skip loop back-edges
            if edge.src == IN_NODE_ID:
                continue  # source node is always available
            if edge.src not in completed:
                return False
        return True

    def _successors(node_id: str) -> list[str]:
        """Return successor node IDs reachable from node_id given current outputs.

        An edge ``when`` is evaluated PORT-LOCALLY against the source node's own
        output ports.  Loop back-edges are handled separately by _activate_loops.
        """
        result: list[str] = []
        for edge in edges_from.get(node_id, []):
            if edge.loop_max is not None:
                continue  # loop edges handled separately
            if edge.dst == OUT_NODE_ID:
                continue  # the $output sink is collected after the run, never scheduled
            if edge.when is None or evaluate(edge.when, _source_ports(edge.src)):
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
                if edge.dst == OUT_NODE_ID:
                    continue  # sink, not a runnable node
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
                if edge.when is not None and not evaluate(edge.when, _source_ports(edge.src)):
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
    # When restoring from a checkpoint, seed pending with the not-yet-completed
    # frontier instead of always starting at the entry node.
    if _ckpt_active and completed:
        pending: set[str] = set()
        for node in wf.nodes:
            if node.id not in completed and _is_ready(node.id):
                pending.add(node.id)
        if not pending and wf.entry not in completed:
            pending = {wf.entry}
    else:
        pending = {wf.entry}
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

    last = stack[-1] if stack else {}
    runtime: dict[str, Any] = {
        "ctx": {
            "step": last.get("step", -1),
            "node_id": last.get("node", ""),
            "workflow_name": wf.name,
        },
        "stack": stack,
        "state": {k: v for k, v in outputs.items() if k != IN_NODE_ID},
        "in": outputs[IN_NODE_ID],
        "out": out_live,
    }
    return ExecResult(runtime=runtime)
