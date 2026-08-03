"""flow.runners — pluggable agent-execution backends.

An agent node's execution is funneled through the :class:`AgentRunner` contract:
given the interpolated prompts, the resolved model, and the node, produce
``(output_value, tokens)``.  Everything else (input assembly, storing, retry,
checkpoint, budget, events) is the executor driver's job.

Two backends implement the contract:

* :class:`SdkRunner` — the in-process ``agent``/``ai`` SDK (the default). Builds an
  ``Agent``, adds the ``submit_result`` tool when the node is structured, and drains
  one turn.  (SDK imports are lazy so a CLI-only future need not load them.)
* CLI runners (added later) shell out to an external coding-agent CLI.

See ``docs/cli-agent.md``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

from flow.errors import WorkflowExecutionError
from flow.models import NodeDef, agent_is_structured, agent_output_schema

if TYPE_CHECKING:
    from agent.core import StreamFn

    from flow.tools import ToolRegistry


class AgentRunner(Protocol):
    """Executes one agent turn: (prompts, model, node) -> (value, tokens).

    The value is the structured object (when the node has structured output ports)
    or the joined assistant text; ``tokens`` is the turn's total token count.
    """

    async def run(
        self,
        node: NodeDef,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout: float,
    ) -> tuple[object, int]: ...


class SdkRunner:
    """The default backend: the in-process ``agent`` + ``ai`` SDK.

    Holds the SDK wiring (stream-fn factory, tool registry, web-search factory) and
    runs each agent turn by building an ``Agent`` and draining it — the exact logic
    that previously lived inline in ``executor._node_agent``.
    """

    def __init__(
        self,
        *,
        stream_fn_factory: Callable[[str], StreamFn],
        tool_registry: ToolRegistry,
        web_search_fn_factory: Callable[[str], Callable[[str], Awaitable[str]]] | None,
    ) -> None:
        self._stream_fn_factory = stream_fn_factory
        self._tool_registry = tool_registry
        self._web_search_fn_factory = web_search_fn_factory

    async def run(
        self,
        node: NodeDef,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout: float,
    ) -> tuple[object, int]:
        from agent.agent import Agent
        from agent.core import AgentConfig, AgentTool
        from agent.events import TurnEndEvent
        from ai.types import AssistantMessage, TextContent

        stream_fn = self._stream_fn_factory(model)
        resolved_tools: tuple[AgentTool, ...] = self._tool_registry.resolve(node.tools)

        tool_ctx: dict[str, object] | None = None
        sink: dict[str, object] = {}
        final_sys_prompt = system_prompt
        structured = agent_is_structured(node)
        if structured:
            from agent.tools import create_submit_result_tool

            resolved_tools = resolved_tools + (create_submit_result_tool(),)
            tool_ctx = {"flow_output_schema": agent_output_schema(node), "flow_result_sink": sink}
            field_names = ", ".join(p.name for p in node.output_ports)
            final_sys_prompt = system_prompt + (
                f"\nWhen finished, you MUST call the submit_result tool"
                f" with an object containing these fields: {field_names}."
            )

        web_search_fn = None
        if node.web_search and self._web_search_fn_factory is not None:
            web_search_fn = self._web_search_fn_factory(node.web_search_model or model)

        agent = Agent(
            stream_fn,
            config=AgentConfig(model=model, system_prompt=final_sys_prompt),
            tools=resolved_tools,
            tool_ctx=tool_ctx,
            web_search_fn=web_search_fn,
        )

        accumulated: list[str] = []
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
                        # Some providers report per-category counts but leave
                        # total_tokens at 0; fall back to input + output.
                        _u = msg.usage
                        total_tokens += _u.total_tokens or (_u.input + _u.output)

        await asyncio.wait_for(_drain(), timeout=timeout)

        if structured:
            result_obj = sink.get("result")
            if result_obj is None:
                raise WorkflowExecutionError(f"Node {node.id!r}: agent did not submit a result via submit_result")
            return result_obj, total_tokens
        return "".join(accumulated), total_tokens
