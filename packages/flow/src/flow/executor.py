"""flow.executor — linear workflow executor."""

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
    """Execute a workflow definition linearly.

    Starts at ``wf.entry`` and follows the single outgoing non-conditional
    edge until there is no successor.

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

    # Build initial state from workflow definition
    state: dict[str, str] = dict(wf.initial_state)
    node_outputs: dict[str, str] = {}

    # Build a lookup of nodes
    node_map = {n.id: n for n in wf.nodes}

    # Build adjacency: src -> list[EdgeDef]
    edges_from: dict[str, list[EdgeDef]] = defaultdict(list)
    for edge in wf.edges:
        edges_from[edge.src].append(edge)

    current_id: str | None = wf.entry

    while current_id is not None:
        node = node_map[current_id]

        # Interpolate prompts from current state
        sys_prompt = interpolate(node.system_prompt, state)
        user_prompt = interpolate(node.prompt, state)

        model = node.model or wf.default_model
        stream_fn = stream_fn_factory(model)

        agent = Agent(
            stream_fn,
            config=AgentConfig(model=model, system_prompt=sys_prompt),
        )

        logger.debug("Running node %r with model %r", node.id, model)

        accumulated: list[str] = []

        async def _run_node() -> None:
            event_stream = await agent.prompt(user_prompt)
            async for event in event_stream:
                if isinstance(event, TurnEndEvent) and event.message is not None:
                    msg = event.message
                    if isinstance(msg, AssistantMessage):
                        for part in msg.content:
                            if isinstance(part, TextContent):
                                accumulated.append(part.text)

        await asyncio.wait_for(_run_node(), timeout=timeout)

        output_text = "".join(accumulated)

        if node.output is not None:
            state[node.output] = output_text
            node_outputs[node.output] = output_text

        # Find next node: first unconditional edge, or first conditional edge
        # whose condition evaluates to True (linear path only)
        current_id = None
        for edge in edges_from.get(node.id, []):
            if edge.when is None:
                current_id = edge.dst
                break
            if evaluate(edge.when, state):
                current_id = edge.dst
                break

    return ExecResult(final_state=state, node_outputs=node_outputs)
