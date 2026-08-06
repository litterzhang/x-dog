"""Tests for the Agent class — constructor, integration with prompt/continue/steering."""

import asyncio
from typing import Any

import pytest
from xdog.agent import AgentConfig
from xdog.agent.agent import Agent
from xdog.ai import AssistantMessage, CostBreakdown, TextContent, Usage, UserMessage
from xdog.ai.types import DoneEvent, StartEvent
from xdog.ai.utils.event_stream import EventStream as AiEventStream

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_MODEL = "mock/test-model"

def _create_usage() -> Usage:
    return Usage(0, 0, 0, 0, 0, CostBreakdown(0, 0, 0, 0, 0))

def _noop_stream_fn(model, context, options):
    return AiEventStream.empty(AssistantMessage(content=()))

def _create_assistant_message(text: str, stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=(TextContent(text=text),),
        api="openai-completions",
        provider="copilot",
        model="mock",
        usage=_create_usage(),
        stop_reason=stop_reason,
        timestamp=0,
    )

def _create_done_stream_fn(text: str = "ok") -> Any:
    def fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        msg = _create_assistant_message(text)
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason="stop", message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    return fn

def _create_blocking_stream_fn() -> tuple[Any, asyncio.Event]:
    release = asyncio.Event()

    def fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            start_msg = _create_assistant_message("", stop_reason="stop")
            await stream.send(StartEvent(partial=start_msg))
            await release.wait()
            error_msg = _create_assistant_message("Aborted", stop_reason="error")
            await stream.send(DoneEvent(stop_reason="error", message=error_msg))
            stream.set_result(error_msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    return fn, release

# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Integration — prompt/continue/steering
# ---------------------------------------------------------------------------

class TestAgentIntegration:

    @pytest.mark.asyncio
    async def test_prompt_while_streaming_raises(self) -> None:
        blocking_fn, release = _create_blocking_stream_fn()
        agent = Agent(blocking_fn, config=AgentConfig(model=_MOCK_MODEL))

        first_stream = await agent.prompt("First message")
        await asyncio.sleep(0.05)

        with pytest.raises(RuntimeError, match="already processing"):
            await agent.prompt("Second message")

        release.set()
        async for _ in first_stream:
            pass

    @pytest.mark.asyncio
    async def test_continue_drains_follow_ups(self) -> None:
        agent = Agent(_create_done_stream_fn("Processed"), config=AgentConfig(model=_MOCK_MODEL))

        agent.replace_messages([
            UserMessage(content=(TextContent(text="Initial"),)),
            _create_assistant_message("Initial response"),
        ])
        agent.follow_up(UserMessage(content=(TextContent(text="Queued follow-up"),)))

        stream = await agent.continue_()
        async for _ in stream:
            pass

        has_follow_up = any(
            hasattr(m, "content") and isinstance(m.content, tuple)
            and any(isinstance(p, TextContent) and p.text == "Queued follow-up" for p in m.content)
            for m in agent.state.messages
        )
        assert has_follow_up

    @pytest.mark.asyncio
    async def test_one_at_a_time_steering(self) -> None:
        response_count = [0]

        def counting_stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            response_count[0] += 1
            msg = _create_assistant_message(f"Processed {response_count[0]}")
            stream: AiEventStream[AssistantMessage] = AiEventStream()

            async def _push() -> None:
                await asyncio.sleep(0)
                await stream.send(DoneEvent(stop_reason="stop", message=msg))
                stream.set_result(msg)
                await stream.close()

            asyncio.ensure_future(_push())
            return stream

        agent = Agent(counting_stream_fn, config=AgentConfig(model=_MOCK_MODEL))

        agent.replace_messages([
            UserMessage(content=(TextContent(text="Initial"),)),
            _create_assistant_message("Initial response"),
        ])

        agent.steer(UserMessage(content=(TextContent(text="Steering 1"),)))
        agent.steer(UserMessage(content=(TextContent(text="Steering 2"),)))

        stream = await agent.continue_()
        async for _ in stream:
            pass

        recent = agent.state.messages[-4:]
        roles = [m.role for m in recent]
        assert roles == ["user", "assistant", "user", "assistant"]
        assert response_count[0] == 2
