import asyncio

import pytest
from ai.providers.testing import make_test_model, register_test_protocol
from ai.types import Context, UserMessage

pytestmark = pytest.mark.asyncio


def mock_stream_fn(model, context, options):
    from ai.types import DoneEvent, TextDeltaEvent, TextDoneEvent
    from ai.utils.event_stream import EventStream

    async def generate():
        yield TextDeltaEvent(delta="Mock ")
        yield TextDeltaEvent(delta="response")
        yield TextDoneEvent(text="Mock response")
        yield DoneEvent(stop_reason="stop")

    stream_obj = EventStream.from_async_generator(generate())
    async def run_and_set():
        import ai.types
        try:
            while not stream_obj.done:
                await asyncio.sleep(0.01)
        finally:
            stream_obj.set_result(ai.types.AssistantMessage(content=(), stop_reason="stop"))
    asyncio.create_task(run_and_set())
    return stream_obj


@pytest.fixture(autouse=True)
def setup_test_protocol():
    register_test_protocol(mock_stream_fn)
    make_test_model("test/stream-model")


async def test_stream_basic():
    import ai
    p = ai.provider("test")
    ctx = Context(messages=(UserMessage(content="Hello"),))
    result_stream = p.stream("stream-model", ctx)

    events = []
    async for event in result_stream:
        events.append(event)

    assert len(events) == 4
    assert events[0].type == "text_delta"
    assert events[0].delta == "Mock "
    assert events[3].type == "done"
    assert events[3].stop_reason == "stop"

    response = await result_stream.result()
    assert response.stop_reason == "stop"
