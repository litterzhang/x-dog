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
import json
import os
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


# ---------------------------------------------------------------------------
# CLI backends — shell out to an external coding-agent CLI for one turn.
# See docs/cli-agent.md.  Adapters own the per-CLI argv + stdout grammar; the
# CliRunner is adapter-agnostic (spawn, feed stdin, check exit, hand to parse).
# ---------------------------------------------------------------------------


class CliAdapter(Protocol):
    """Maps the agent-turn contract onto one CLI's argv + stdout grammar."""

    name: str

    def binary(self) -> str:
        """Default executable name (overridden by FLOW_CLI_BIN)."""
        ...

    def argv(
        self,
        *,
        model: str,
        system_prompt: str,
        output_schema: dict[str, object] | None,
        allowed_tools: tuple[str, ...],
    ) -> list[str]:
        """Command-line args after the binary (prompt is fed on stdin)."""
        ...

    def stdin(self, *, system_prompt: str, user_prompt: str) -> str:
        """The text piped to the CLI's stdin."""
        ...

    def parse(self, stdout: str, *, structured: bool) -> tuple[object, int]:
        """Parse the CLI's stdout into (value, tokens)."""
        ...


class ClaudeAdapter:
    """Adapter for the ``claude`` CLI (Claude Code) in headless print mode."""

    name = "claude-cli"

    def binary(self) -> str:
        return "claude"

    def argv(
        self,
        *,
        model: str,
        system_prompt: str,
        output_schema: dict[str, object] | None,
        allowed_tools: tuple[str, ...],
    ) -> list[str]:
        args = ["-p", "--output-format", "json"]
        if model:
            args += ["--model", model]
        if system_prompt:
            args += ["--append-system-prompt", system_prompt]
        if output_schema is not None:
            args += ["--json-schema", json.dumps(output_schema, sort_keys=True)]
        if allowed_tools:
            args += ["--allowedTools", ",".join(allowed_tools)]
        return args

    def stdin(self, *, system_prompt: str, user_prompt: str) -> str:
        # claude takes the system prompt via a flag, so stdin is the user prompt.
        return user_prompt

    def parse(self, stdout: str, *, structured: bool) -> tuple[object, int]:
        env = json.loads(stdout)
        tokens = 0
        if isinstance(env, dict):
            _i = env.get("input_tokens")
            _o = env.get("output_tokens")
            tokens = (int(_i) if isinstance(_i, int) else 0) + (int(_o) if isinstance(_o, int) else 0)
            if structured:
                obj = env.get("structured_output")
                if obj is None:
                    raise WorkflowExecutionError("claude-cli: no structured_output in response")
                return obj, tokens
            result = env.get("result", "")
            return (result if isinstance(result, str) else json.dumps(result)), tokens
        raise WorkflowExecutionError("claude-cli: unexpected output (not a JSON object)")


_CLI_ADAPTERS: dict[str, CliAdapter] = {a.name: a for a in (ClaudeAdapter(),)}


def cli_adapter_for(backend: str) -> CliAdapter:
    """Return the adapter for a ``node.backend`` value, or raise."""
    adapter = _CLI_ADAPTERS.get(backend)
    if adapter is None:
        known = ", ".join(sorted(_CLI_ADAPTERS)) or "<none>"
        raise WorkflowExecutionError(f"unknown CLI backend {backend!r}; known: {known}")
    return adapter


class CliRunner:
    """Runs an agent turn by shelling out to a coding-agent CLI (adapter-driven).

    Imports no ``agent``/``ai`` — only ``asyncio`` subprocess + ``json``.  The CLI
    binary is the adapter's default, overridable per-backend by ``FLOW_CLI_BIN`` (or
    ``FLOW_CLI_BIN_<BACKEND>``) — the test-stub hook.
    """

    def __init__(self, adapter: CliAdapter) -> None:
        self._adapter = adapter

    def _resolve_binary(self) -> str:
        env_key = "FLOW_CLI_BIN_" + self._adapter.name.upper().replace("-", "_")
        return os.environ.get(env_key) or os.environ.get("FLOW_CLI_BIN") or self._adapter.binary()

    async def run(
        self,
        node: NodeDef,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout: float,
    ) -> tuple[object, int]:
        structured = agent_is_structured(node)
        schema = agent_output_schema(node) if structured else None
        argv = self._adapter.argv(
            model=model,
            system_prompt=system_prompt,
            output_schema=schema,
            allowed_tools=node.allowed_tools,
        )
        stdin_text = self._adapter.stdin(system_prompt=system_prompt, user_prompt=user_prompt)
        binary = self._resolve_binary()

        proc = await asyncio.create_subprocess_exec(
            binary,
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout=timeout)
        except TimeoutError:
            proc.kill()
            raise WorkflowExecutionError(f"Node {node.id!r}: CLI {binary!r} timed out after {timeout}s") from None
        if proc.returncode != 0:
            err = err_b.decode(errors="replace").strip()
            raise WorkflowExecutionError(
                f"Node {node.id!r}: CLI {binary!r} exited {proc.returncode}: {err or '(no stderr)'}"
            )
        return self._adapter.parse(out_b.decode(errors="replace"), structured=structured)
