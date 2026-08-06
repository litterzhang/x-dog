"""Tests for orchestrator."""
import asyncio

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, Model, ModelCost, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.channel import TuiChannel
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, GroupConfig, QueueMode, UserInput

_TEST_MODEL = Model(
    id="test/dummy", name="dummy", api="openai-completions",
    provider="test", context_window=200_000, max_tokens=16_384,
    cost=ModelCost(),
)

def _make_mock_stream_fn(response_text="I am the assistant."):
    def stream_fn(model_id, context, options=None):
        msg = AssistantMessage(content=(TextContent(text=response_text),))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    return stream_fn

@pytest.fixture
def orch(tmp_path):
    config = ClawConfig(data_dir=str(tmp_path / "data"))
    o = Orchestrator(config, model=_TEST_MODEL, stream_fn=_make_mock_stream_fn(), data_dir=tmp_path / "data")
    o.register_group(Group(id="main", name="Main", is_main=True))
    return o

@pytest.mark.asyncio
async def test_handle_message_returns_response(orch):
    result = await orch.route_message(UserInput(group_id="main", content="hello", sender="user"))
    assert result is not None
    assert result.response_text

# ---------------------------------------------------------------------------
# Steer mode routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_steer_mode_calls_executor_steer(tmp_path):
    """When agent is running and STEER mode is configured, executor.steer() is called."""
    config = ClawConfig(data_dir=str(tmp_path / "data"))
    steer_config = GroupConfig(queue_mode=QueueMode.STEER)

    # Use a slow stream_fn so the agent is "running" when second message arrives
    call_count = [0]

    def slow_stream_fn(model_id, context, options=None):
        call_count[0] += 1
        msg = AssistantMessage(content=(TextContent(text="response"),))

        async def _gen():
            if call_count[0] == 1:
                # First call: simulate slow processing
                await asyncio.sleep(0.1)
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    o = Orchestrator(config, model=_TEST_MODEL, stream_fn=slow_stream_fn, data_dir=tmp_path / "data")
    o.register_group(Group(id="steer_group", name="SteerGroup", config=steer_config))

    sent = []
    ch = TuiChannel()

    async def capture_send(group_id, text):
        sent.append(text)

    ch.send_message = capture_send
    o.add_channel(ch)

    steered = []
    runtime = o._runtimes["steer_group"]
    original_steer = runtime.steer
    def track_steer(content):
        steered.append(content)
        return original_steer(content)
    runtime.steer = track_steer

    # Send first message (starts the agent turn)
    task1 = asyncio.create_task(
        o.route_message(UserInput(group_id="steer_group", content="first", sender="user"))
    )
    await asyncio.sleep(0.02)  # let the first message start processing

    # Send second message while agent is running — should trigger steer
    await o.route_message(UserInput(group_id="steer_group", content="urgent", sender="user"))
    await task1

    assert len(steered) == 1
    assert steered[0] == "urgent"

    await o.stop()
