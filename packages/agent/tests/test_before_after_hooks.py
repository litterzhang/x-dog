"""Tests for before_tool_call and after_tool_call hooks.

Updated to use the new agent_loop(prompts, context, config, stream_fn=...) API.
"""

import asyncio
from typing import Any

import pytest
from agent import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ToolExecutionEndEvent,
)
from agent.agent_loop import agent_loop
from ai import (
    AssistantMessage,
    CostBreakdown,
    Message,
    TextContent,
    ToolCall,
    Usage,
    UserMessage,
)
from ai.types import DoneEvent, StreamOptions, ToolResultMessage
from ai.utils.event_stream import EventStream as AiEventStream


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
async def test_before_tool_call_blocking():
    """before_tool_call hook can block tool execution."""
    tool_executed = False

    async def tool_execute(tool_call_id: str, args: dict, cancel: Any = None, on_update: Any = None, **kwargs: Any) -> AgentToolResult:
        nonlocal tool_executed
        tool_executed = True
        return AgentToolResult(content=(TextContent(text="executed"),))

    tool = AgentTool(name="dangerous_tool", description="Dangerous", execute=tool_execute)

    async def before_hook(ctx: BeforeToolCallContext, cancel: Any) -> BeforeToolCallResult:
        assert ctx.tool_call.name == "dangerous_tool"
        assert isinstance(ctx.assistant_message, AssistantMessage)
        return BeforeToolCallResult(block=True, reason="Tool blocked for safety")

    context = AgentContext(
        system_prompt="test",
        messages=[],
        tools=[tool],
    )

    config = AgentLoopConfig(
        convert_to_llm=identity_converter,
        before_tool_call=before_hook,
    )

    stream_fn = _create_stream_fn_from_responses([
        create_assistant_message([
            ToolCall(id="tc1", name="dangerous_tool", arguments={"x": 1}),
        ], "toolUse"),
        create_assistant_message([TextContent(text="OK")]),
    ])

    events: list[AgentEvent] = []
    async for event in agent_loop(
        [UserMessage(content="Do dangerous thing")],
        context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions(),
    ):
        events.append(event)

    # Tool should NOT have been executed
    assert not tool_executed

    # Should have error result with our reason
    end_events = [e for e in events if isinstance(e, ToolExecutionEndEvent)]
    assert len(end_events) >= 1
    assert end_events[0].is_error
    assert end_events[0].result is not None
    assert any(
        "blocked" in str(p.text).lower()
        for p in end_events[0].result.content
        if isinstance(p, TextContent)
    )

@pytest.mark.asyncio
async def test_after_tool_call_override_result():
    """after_tool_call hook can override the tool result."""
    async def tool_execute(tool_call_id: str, args: dict, cancel: Any = None, on_update: Any = None, **kwargs: Any) -> AgentToolResult:
        return AgentToolResult(content=(TextContent(text="original"),))

    tool = AgentTool(name="my_tool", description="Tool", execute=tool_execute)

    async def after_hook(ctx: AfterToolCallContext, cancel: Any) -> AfterToolCallResult:
        assert ctx.tool_call.name == "my_tool"
        assert not ctx.is_error
        return AfterToolCallResult(
            content=(TextContent(text="overridden"),),
            is_error=False,
        )

    context = AgentContext(
        system_prompt="test",
        messages=[],
        tools=[tool],
    )

    config = AgentLoopConfig(
        convert_to_llm=identity_converter,
        after_tool_call=after_hook,
    )

    stream_fn = _create_stream_fn_from_responses([
        create_assistant_message([
            ToolCall(id="tc1", name="my_tool", arguments={}),
        ], "toolUse"),
        create_assistant_message([TextContent(text="Done")]),
    ])

    events: list[AgentEvent] = []
    async for event in agent_loop(
        [UserMessage(content="Go")],
        context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions(),
    ):
        events.append(event)

    # The AgentEndEvent messages should contain the overridden result
    end_events = [e for e in events if isinstance(e, AgentEndEvent)]
    assert len(end_events) == 1
    messages = end_events[0].messages
    tool_results = [m for m in messages if isinstance(m, ToolResultMessage)]
    assert len(tool_results) >= 1
    assert any(
        isinstance(p, TextContent) and p.text == "overridden"
        for p in tool_results[0].content
    )

@pytest.mark.asyncio
async def test_after_tool_call_mark_as_error():
    """after_tool_call hook can override is_error flag."""
    async def tool_execute(tool_call_id: str, args: dict, cancel: Any = None, on_update: Any = None, **kwargs: Any) -> AgentToolResult:
        return AgentToolResult(content=(TextContent(text="ok"),))

    tool = AgentTool(name="my_tool", description="Tool", execute=tool_execute)

    async def after_hook(ctx: AfterToolCallContext, cancel: Any) -> AfterToolCallResult:
        # Mark as error even though execution succeeded
        return AfterToolCallResult(is_error=True)

    context = AgentContext(
        system_prompt="test",
        messages=[],
        tools=[tool],
    )

    config = AgentLoopConfig(
        convert_to_llm=identity_converter,
        after_tool_call=after_hook,
    )

    stream_fn = _create_stream_fn_from_responses([
        create_assistant_message([
            ToolCall(id="tc1", name="my_tool", arguments={}),
        ], "toolUse"),
        create_assistant_message([TextContent(text="Done")]),
    ])

    events: list[AgentEvent] = []
    async for event in agent_loop(
        [UserMessage(content="Go")],
        context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions(),
    ):
        events.append(event)

    end_events = [e for e in events if isinstance(e, AgentEndEvent)]
    assert len(end_events) == 1
    tool_results = [m for m in end_events[0].messages if isinstance(m, ToolResultMessage)]
    assert len(tool_results) >= 1
    assert tool_results[0].is_error
