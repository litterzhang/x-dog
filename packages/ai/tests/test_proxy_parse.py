"""Regression tests for the Anthropic proxy message parsing.

Covers two bugs found while driving Claude Code through the Copilot-backed
proxy:

1. Parallel tool calls: a single Anthropic ``user`` message may bundle several
   ``tool_result`` blocks (one per parallel ``tool_use``). The parser must emit
   *all* of them, otherwise the upstream API rejects the turn with
   ``tool_use ids were found without tool_result blocks``.

2. Thinking-signature passthrough: when the streamed assistant turn contains a
   ``thinking`` block, its ``signature`` must survive the round-trip so the
   client can replay it. (Handled in the anthropic_messages parser; here we at
   least assert the proxy preserves the signature on parse.)
"""

from ai.proxy import _parse_message, _parse_upstream_error
from ai.types import (
    AssistantMessage,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def test_parse_message_emits_all_parallel_tool_results():
    """Two tool_result blocks in one user message -> two ToolResultMessages."""
    msg = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "result one"},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "result two"},
        ],
    }
    parsed = _parse_message(msg)
    tool_results = [m for m in parsed if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 2
    assert [t.tool_call_id for t in tool_results] == ["toolu_1", "toolu_2"]
    assert tool_results[0].content[0].text == "result one"
    assert tool_results[1].content[0].text == "result two"


def test_parse_message_tool_results_then_text():
    """Mixed tool_result + text -> tool results first, then a UserMessage."""
    msg = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "r1"},
            {"type": "text", "text": "follow-up note"},
        ],
    }
    parsed = _parse_message(msg)
    assert isinstance(parsed[0], ToolResultMessage)
    assert isinstance(parsed[-1], UserMessage)
    assert parsed[-1].content[0].text == "follow-up note"


def test_parse_message_plain_user_string():
    parsed = _parse_message({"role": "user", "content": "hello"})
    assert len(parsed) == 1
    assert isinstance(parsed[0], UserMessage)
    assert parsed[0].content == "hello"


def test_parse_message_assistant_thinking_preserves_signature():
    """Assistant thinking block keeps its signature on parse."""
    msg = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "reasoning...", "signature": "SIG123"},
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}},
        ],
    }
    parsed = _parse_message(msg)
    assert len(parsed) == 1
    asst = parsed[0]
    assert isinstance(asst, AssistantMessage)
    thinking = [p for p in asst.content if isinstance(p, ThinkingContent)]
    tool_calls = [p for p in asst.content if isinstance(p, ToolCall)]
    assert thinking and thinking[0].thinking_signature == "SIG123"
    assert tool_calls and tool_calls[0].id == "toolu_1"


def test_parse_message_empty_user_content():
    parsed = _parse_message({"role": "user", "content": []})
    assert len(parsed) == 1
    assert isinstance(parsed[0], UserMessage)
    assert parsed[0].content == ""


def test_parse_upstream_context_overflow_error():
    error = _parse_upstream_error(
        'HTTP 400: {"error":{"code":"model_max_prompt_tokens_exceeded",'
        '"message":"prompt is too long","type":"invalid_request_error"},'
        '"request_id":"req_123","type":"error"}'
    )

    assert error == (
        400,
        {
            "type": "error",
            "error": {
                "code": "model_max_prompt_tokens_exceeded",
                "message": "prompt is too long",
                "type": "invalid_request_error",
            },
            "request_id": "req_123",
        },
    )


def test_parse_upstream_error_falls_back_to_api_error():
    status, error = _parse_upstream_error("network failed")

    assert status == 500
    assert error == {
        "type": "error",
        "error": {"type": "api_error", "message": "network failed"},
    }
