"""Tests for messages serialization (ai.types <-> dicts)."""

from xdog.ai.types import (
    AssistantMessage,
    CostBreakdown,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from xdog.coding.core.messages import dicts_to_messages, messages_to_dicts


def test_message_serialization():
    original = [
        UserMessage(content="sys prompt"),
        AssistantMessage(
            content=(
                ThinkingContent(thinking="hmmm"),
                TextContent(text="i think so"),
                ToolCall(id="t1", name="read_file", arguments={"path": "a.txt"}),
            ),
            api="openai-responses",
            model="gpt-test",
            provider="copilot",
            response_id="response-1",
            usage=Usage(
                input=120,
                output=30,
                cache_read=400,
                cache_write=10,
                total_tokens=560,
                cost=CostBreakdown(total=0.25),
            ),
        ),
        ToolResultMessage(
            tool_call_id="t1",
            tool_name="read_file",
            content=(TextContent(text="file content"),),
        ),
    ]

    dicts = messages_to_dicts(original)

    assert len(dicts) == 3
    assert dicts[1]["role"] == "assistant"
    assert len(dicts[1]["content"]) == 3
    assert dicts[1]["content"][0]["type"] == "thinking"
    assert dicts[1]["content"][1]["type"] == "text"
    assert dicts[1]["content"][2]["type"] == "toolCall"

    restored = dicts_to_messages(dicts)
    assert len(restored) == 3
    assert restored[0].role == "user"
    assert isinstance(restored[1], AssistantMessage)
    assert isinstance(restored[1].content[0], ThinkingContent)
    assert isinstance(restored[1].content[2], ToolCall)
    assert restored[1].api == "openai-responses"
    assert restored[1].response_id == "response-1"
    assert restored[1].usage.input == 120
    assert restored[1].usage.cache_read == 400
    assert restored[1].usage.total_tokens == 560
    assert restored[1].usage.cost.total == 0.25
    assert isinstance(restored[2], ToolResultMessage)
