import json

from ai.proxy import stream_to_sse
from ai.types import (
    AssistantMessage,
    DoneEvent,
    StartEvent,
    TextStartEvent,
    Usage,
)


async def _collect(events):
    async def generate():
        for event in events:
            yield event

    return [chunk async for chunk in stream_to_sse(generate(), "copilot/claude-opus-5")]


async def test_message_start_reports_upstream_input_usage():
    partial = AssistantMessage(usage=Usage(
        input=1234,
        output=0,
        cache_read=5678,
        cache_write=90,
    ))

    chunks = await _collect([
        StartEvent(partial=AssistantMessage()),
        TextStartEvent(index=0, partial=partial),
    ])

    payload = json.loads(chunks[0].decode().split("data: ", 1)[1])

    assert payload["message"]["usage"] == {
        "input_tokens": 1234,
        "output_tokens": 0,
        "cache_read_input_tokens": 5678,
        "cache_creation_input_tokens": 90,
    }


async def test_message_delta_reports_final_input_and_cache_usage():
    final = AssistantMessage(usage=Usage(
        input=999,
        output=42,
        cache_read=7,
        cache_write=3,
    ))

    chunks = await _collect([
        StartEvent(partial=AssistantMessage()),
        TextStartEvent(index=0, partial=AssistantMessage()),
        DoneEvent(stop_reason="stop", message=final),
    ])

    deltas = [
        json.loads(chunk.decode().split("data: ", 1)[1])
        for chunk in chunks
        if b"message_delta" in chunk
    ]

    assert deltas[0]["usage"] == {
        "input_tokens": 999,
        "output_tokens": 42,
        "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 3,
    }
