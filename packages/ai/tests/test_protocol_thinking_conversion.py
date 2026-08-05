import json

from ai.protocols.anthropic_messages import context_to_anthropic
from ai.protocols.openai_responses import context_to_responses_input
from ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
)


def test_anthropic_serializes_signed_thinking_as_native_thinking():
    context = Context(messages=(
        AssistantMessage(content=(
            ThinkingContent(thinking="Analyze this.", thinking_signature="signed-thinking"),
        )),
    ))

    _, messages, _ = context_to_anthropic(context, Model())

    assert messages == [{
        "role": "assistant",
        "content": [{
            "type": "thinking",
            "thinking": "Analyze this.",
            "signature": "signed-thinking",
        }],
    }]


def test_anthropic_serializes_unsigned_nonempty_thinking_as_text():
    context = Context(messages=(
        AssistantMessage(content=(
            ThinkingContent(thinking="Analyze this."),
        )),
    ))

    _, messages, _ = context_to_anthropic(context, Model())

    assert messages == [{
        "role": "assistant",
        "content": [{
            "type": "text",
            "text": "Analyze this.",
        }],
    }]


def test_openai_responses_restores_reasoning_item_from_thinking_signature():
    reasoning_item = {
        "id": "rs_123",
        "type": "reasoning",
        "summary": [{
            "type": "summary_text",
            "text": "Checked the facts.",
        }],
        "encrypted_content": "encrypted-payload",
        "status": "completed",
    }
    context = Context(messages=(
        AssistantMessage(content=(
            ThinkingContent(
                thinking="Checked the facts.",
                thinking_signature=json.dumps(reasoning_item),
            ),
        )),
    ))

    items = context_to_responses_input(
        context,
        Model(api="openai-responses", reasoning=True),
    )

    assert items == [reasoning_item]


def test_anthropic_normalizes_tool_call_and_result_ids_together():
    context = Context(messages=(
        AssistantMessage(content=(
            ToolCall(id="call_123|item_456", name="read_file", arguments={}),
        )),
        ToolResultMessage(
            tool_call_id="call_123|item_456",
            tool_name="read_file",
            content=(TextContent(text="done"),),
        ),
    ))

    _, messages, _ = context_to_anthropic(context, Model())

    assert messages[0]["content"][0]["id"] == "call_123"
    assert messages[1]["content"][0]["tool_use_id"] == "call_123"
