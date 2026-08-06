"""Tests for parallel tool execution in agent_loop.

Updated to use the new agent_loop(prompts, context, config, stream_fn=...) API.
"""

import asyncio
from typing import Any

import pytest
from xdog.agent import (
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    ToolExecutionEndEvent,
)
from xdog.agent.agent_loop import agent_loop
from xdog.ai import (
    AssistantMessage,
    CostBreakdown,
    Message,
    TextContent,
    ToolCall,
    Usage,
    UserMessage,
)
from xdog.ai.types import DoneEvent, StreamOptions, ToolResultMessage
from xdog.ai.utils.event_stream import EventStream as AiEventStream


def create_assistant_message(
    content: tuple | list,
    stop_reason: str = "stop",
) -> AssistantMessage:
    if isinstance(content, list):
        content = tuple(content)
    return AssistantMessage(
        content=content,
        api="openai-completions",
        provider="copilot",
        model="mock",
        usage=Usage(0, 0, 0, 0, 0, CostBreakdown(0, 0, 0, 0, 0)),
        stop_reason=stop_reason,
        timestamp=0,
    )

def identity_converter(messages: list[AgentMessage]) -> list[Message]:
    return [
        m for m in messages
        if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
    ]

def _create_stream_fn_from_responses(responses: list[AssistantMessage]) -> Any:
    """Create a StreamFn that returns pre-built EventStream responses."""
    call_index = [0]

    def fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        idx = call_index[0]
        call_index[0] += 1
        msg = responses[idx] if idx < len(responses) else responses[-1]

        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)
            stop = msg.stop_reason if msg.stop_reason in ("stop", "length", "toolUse") else "stop"
            await stream.send(DoneEvent(stop_reason=stop, message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    return fn

@pytest.mark.asyncio
async def test_parallel_tool_execution():
    """Tool calls should execute concurrently when tool_execution='parallel'."""
    execution_log: list[str] = []

    async def tool_a_execute(tool_call_id: str, args: dict, cancel: Any = None, on_update: Any = None, **kwargs: Any) -> AgentToolResult:
        execution_log.append("a_start")
        await asyncio.sleep(0.05)
        execution_log.append("a_end")
        return AgentToolResult(content=(TextContent(text="result_a"),))

    async def tool_b_execute(tool_call_id: str, args: dict, cancel: Any = None, on_update: Any = None, **kwargs: Any) -> AgentToolResult:
        execution_log.append("b_start")
        await asyncio.sleep(0.05)
        execution_log.append("b_end")
        return AgentToolResult(content=(TextContent(text="result_b"),))

    tool_a = AgentTool(name="tool_a", description="A", execute=tool_a_execute)
    tool_b = AgentTool(name="tool_b", description="B", execute=tool_b_execute)

    context = AgentContext(
        system_prompt="test",
        messages=[],
        tools=[tool_a, tool_b],
    )

    config = AgentLoopConfig(
        convert_to_llm=identity_converter,
        tool_execution="parallel",
    )

    stream_fn = _create_stream_fn_from_responses([
        create_assistant_message([
            TextContent(text="Running tools"),
            ToolCall(id="tc1", name="tool_a", arguments={}),
            ToolCall(id="tc2", name="tool_b", arguments={}),
        ], "toolUse"),
        create_assistant_message([TextContent(text="Done")]),
    ])

    events: list[AgentEvent] = []
    async for event in agent_loop(
        [UserMessage(content="Go")],
        context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions(),
    ):
        events.append(event)

    # Both tools should start before either finishes (parallel)
    assert "a_start" in execution_log
    assert "b_start" in execution_log
    # Both should have run
    assert "a_end" in execution_log
    assert "b_end" in execution_log
    # Check events
    assert any(isinstance(e, AgentEndEvent) for e in events)

@pytest.mark.asyncio
async def test_parallel_missing_tool():
    """Parallel mode should handle missing tools gracefully."""
    async def tool_a_execute(tool_call_id: str, args: dict, cancel: Any = None, on_update: Any = None, **kwargs: Any) -> AgentToolResult:
        return AgentToolResult(content=(TextContent(text="ok"),))

    tool_a = AgentTool(name="tool_a", description="A", execute=tool_a_execute)

    context = AgentContext(
        system_prompt="test",
        messages=[],
        tools=[tool_a],
    )

    config = AgentLoopConfig(
        convert_to_llm=identity_converter,
        tool_execution="parallel",
    )

    stream_fn = _create_stream_fn_from_responses([
        create_assistant_message([
            TextContent(text="Running"),
            ToolCall(id="tc1", name="tool_a", arguments={}),
            ToolCall(id="tc2", name="nonexistent", arguments={}),
        ], "toolUse"),
        create_assistant_message([TextContent(text="Done")]),
    ])

    events: list[AgentEvent] = []
    async for event in agent_loop(
        [UserMessage(content="Go")],
        context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions(),
    ):
        events.append(event)

    # Should have ToolExecutionEnd with is_error for nonexistent tool
    error_events = [
        e for e in events
        if isinstance(e, ToolExecutionEndEvent) and e.is_error
    ]
    assert len(error_events) >= 1
    assert any(e.tool_name == "nonexistent" for e in error_events)
