"""Tests for the imperative run_agent_loop / run_agent_loop_continue API."""

import asyncio
import pytest
from typing import Any, List

from agent.agent_loop import run_agent_loop, run_agent_loop_continue
from agent import (
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentStartEvent,
)
from ai.types import (
    StreamOptions,
    AssistantMessage,
    CostBreakdown,
    DoneEvent,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from ai.utils.event_stream import EventStream as AiEventStream

def create_model():
    return Model(
        id="mock",
        name="mock",
        api="openai-completions",
        provider="copilot",
        base_url="https://example.invalid",
        reasoning=False,
        input=("text",),
        cost={"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        context_window=8192,
        max_tokens=2048,
    )

def create_assistant_message(content, stop_reason="stop"):
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

def identity_converter(messages: List[AgentMessage]) -> List[Message]:
    return [
        m for m in messages
        if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
    ]

def create_mock_stream_fn(response_msg: AssistantMessage) -> Any:
    """Create a stream_fn that returns the given response."""
    def fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)
            stop = response_msg.stop_reason if response_msg.stop_reason in ("stop", "length", "toolUse") else "stop"
            await stream.send(DoneEvent(stop_reason=stop, message=response_msg))
            stream.set_result(response_msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    return fn

@pytest.mark.asyncio
async def test_run_agent_loop_returns_messages():
    """run_agent_loop should return the final message list."""
    response = create_assistant_message((TextContent(text="Hello back"),))
    stream_fn = create_mock_stream_fn(response)

    context = AgentContext(
        system_prompt="You are helpful.",
        messages=[UserMessage(content="Hello")],
        tools=[],
    )

    config = AgentLoopConfig(
        convert_to_llm=identity_converter,
    )

    events: list[AgentEvent] = []

    async def emit(event: AgentEvent) -> None:
        events.append(event)

    messages = await run_agent_loop(
        [UserMessage(content="Hello")], context, config, emit,
        stream_fn=stream_fn, model="mock/test-model", options=StreamOptions(),
    )

    # Should have the prompt user message + assistant response
    assert len(messages) >= 2
    assert isinstance(messages[-1], AssistantMessage)

    # Events should include start and end
    assert any(isinstance(e, AgentStartEvent) for e in events)
    assert any(isinstance(e, AgentEndEvent) for e in events)

@pytest.mark.asyncio
async def test_run_agent_loop_continue():
    """run_agent_loop_continue should work identically to run_agent_loop."""
    response = create_assistant_message((TextContent(text="Continued"),))
    stream_fn = create_mock_stream_fn(response)

    context = AgentContext(
        system_prompt="test",
        messages=[
            UserMessage(content="Hi"),
            create_assistant_message((TextContent(text="Hello"),)),
            UserMessage(content="Continue"),
        ],
        tools=[],
    )

    config = AgentLoopConfig(
        convert_to_llm=identity_converter,
    )

    events: list[AgentEvent] = []

    async def emit(event: AgentEvent) -> None:
        events.append(event)

    messages = await run_agent_loop_continue(
        context, config, emit, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions(),
    )

    # Should have at least the new assistant message
    assert len(messages) >= 1
    assert any(isinstance(e, AgentEndEvent) for e in events)

@pytest.mark.asyncio
async def test_run_agent_loop_cancellation():
    """run_agent_loop should respect cancellation."""
    response = create_assistant_message((TextContent(text="Never"),))

    def slow_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(10)
            await stream.send(DoneEvent(stop_reason="stop", message=response))
            stream.set_result(response)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    context = AgentContext(
        system_prompt="test",
        messages=[UserMessage(content="Go")],
        tools=[],
    )

    config = AgentLoopConfig(
        convert_to_llm=identity_converter,
    )

    cancel = asyncio.Event()
    cancel.set()  # Cancel immediately

    events: list[AgentEvent] = []

    async def emit(event: AgentEvent) -> None:
        events.append(event)

    messages = await run_agent_loop(
        [UserMessage(content="Go")], context, config, emit,
        stream_fn=slow_fn, model="mock/test-model", options=StreamOptions(), cancel=cancel,
    )

    # Should have ended early
    assert any(isinstance(e, AgentEndEvent) for e in events)
