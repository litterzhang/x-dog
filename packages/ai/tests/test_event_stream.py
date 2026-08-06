import asyncio

import pytest
from xdog.ai.types import TextDeltaEvent, TextDoneEvent
from xdog.ai.utils.event_stream import EventStream

pytestmark = pytest.mark.asyncio

async def test_event_stream_async_generator():
    async def generate():
        yield TextDeltaEvent(delta="Hello")
        yield TextDoneEvent(text="Hello")

    stream = EventStream.from_async_generator(generate())

    events = await stream.collect()
    assert len(events) == 2
    assert events[0].type == "text_delta"
    assert events[1].type == "text_done"

async def test_event_stream_push_based():
    stream = EventStream()

    async def producer():
        await stream.send(TextDeltaEvent(delta="World"))
        await stream.send(TextDoneEvent(text="World"))
        await stream.close()

    asyncio.create_task(producer())

    events = await stream.collect()
    assert len(events) == 2
    assert events[0].delta == "World"

async def test_event_stream_listeners():
    stream = EventStream()

    received = []
    def listener(event):
        received.append(event)

    stream.on(listener)

    stream.send_sync(TextDeltaEvent(delta="Sync"))
    stream.close_sync()

    events = await stream.collect()
    assert len(events) == 1
    assert len(received) == 1
    assert received[0].delta == "Sync"

