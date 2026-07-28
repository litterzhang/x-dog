"""Tests for messages serialization (ai.types <-> dicts)."""

from ai.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from coding.core.messages import dicts_to_messages, messages_to_dicts


def test_message_serialization():
    original = [
        UserMessage(content="sys prompt"),
        AssistantMessage(content=(
            ThinkingContent(thinking="hmmm"),
            TextContent(text="i think so"),
            ToolCall(id="t1", name="read_file", arguments={"path": "a.txt"}),
        )),
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
    assert isinstance(restored[2], ToolResultMessage)
