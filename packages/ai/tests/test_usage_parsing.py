from types import SimpleNamespace

from xdog.ai.protocols._message_builder import MessageBuilder
from xdog.ai.protocols.anthropic_messages import _handle_sse_event
from xdog.ai.protocols.openai_completions import _parse_chunk_usage
from xdog.ai.types import Model


def test_parse_chunk_usage_excludes_cached_tokens_from_input():
    raw = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=300,
        total_tokens=1300,
        prompt_tokens_details=SimpleNamespace(cached_tokens=800),
        completion_tokens_details=None,
    )

    usage = _parse_chunk_usage(raw)

    assert usage.input == 200
    assert usage.cache_read == 800


def test_parse_chunk_usage_does_not_double_count_reasoning_tokens():
    raw = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=300,
        total_tokens=1300,
        prompt_tokens_details=SimpleNamespace(cached_tokens=800),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=200),
    )

    usage = _parse_chunk_usage(raw)

    assert usage.output == 300
    assert usage.total_tokens == 1300


def test_anthropic_message_start_populates_total_tokens():
    output = MessageBuilder(Model())

    _handle_sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 2,
                    "cache_read_input_tokens": 30,
                    "cache_creation_input_tokens": 5,
                },
            },
        },
        output,
        -1,
        None,
    )

    assert output.usage.total_tokens == 137


def test_anthropic_message_delta_keeps_total_tokens_current():
    output = MessageBuilder(Model())

    _handle_sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 30,
                    "cache_creation_input_tokens": 5,
                },
            },
        },
        output,
        -1,
        None,
    )
    _handle_sse_event(
        "message_delta",
        {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 64}},
        output,
        -1,
        None,
    )

    assert output.usage.total_tokens == 199
