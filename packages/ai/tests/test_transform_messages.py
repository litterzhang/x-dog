"""Tests for ai.protocols.transform_messages — the message transformation pipeline."""

from __future__ import annotations

from xdog.ai.protocols._transform_messages import (
    context_to_openai,
    transform_messages,
)
from xdog.ai.types import (
    AssistantMessage,
    Context,
    Model,
    OpenAICompletionsCompat,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

_COPILOT_MODEL = Model(
    id="copilot/claude-sonnet-4.6",
    name="Test Model",
    api="openai-completions",
    provider="copilot",
    reasoning=True,
    compat=OpenAICompletionsCompat(supports_developer_role=True),
)

def _assistant(content=(), *, model_id="copilot/claude-sonnet-4.6",
               provider="copilot", api="openai-completions", stop_reason="stop"):
    return AssistantMessage(content=content, model=model_id, provider=provider,
                            api=api, stop_reason=stop_reason)

def test_error_messages_skipped():
    msgs = [
        UserMessage(content="hello"),
        _assistant(content=(TextContent(text="partial"),), stop_reason="error"),
        UserMessage(content="retry"),
    ]
    result = transform_messages(msgs, _COPILOT_MODEL)
    assert len(result) == 2
    assert all(isinstance(m, UserMessage) for m in result)

def test_orphan_tool_call_gets_synthetic_result():
    msgs = [
        _assistant(content=(
            TextContent(text="checking"),
            ToolCall(id="tc_1", name="read_file", arguments={"path": "foo"}),
        )),
        UserMessage(content="what happened?"),
    ]
    result = transform_messages(msgs, _COPILOT_MODEL)
    assert len(result) == 3
    assert isinstance(result[1], ToolResultMessage)
    assert result[1].is_error is True

def test_same_model_keeps_thinking():
    msgs = [_assistant(content=(ThinkingContent(thinking="reason"), TextContent(text="answer")))]
    result = transform_messages(msgs, _COPILOT_MODEL)
    thinking = [b for b in result[0].content if isinstance(b, ThinkingContent)]
    assert len(thinking) == 1

def test_cross_model_converts_thinking_to_text():
    msgs = [_assistant(content=(ThinkingContent(thinking="reason"), TextContent(text="answer")),
                       model_id="other-model", provider="other", api="other-api")]
    result = transform_messages(msgs, _COPILOT_MODEL)
    thinking = [b for b in result[0].content if isinstance(b, ThinkingContent)]
    text = [b for b in result[0].content if isinstance(b, TextContent)]
    assert len(thinking) == 0
    assert len(text) == 2

def test_context_to_openai_system_prompt():
    ctx = Context(system_prompt="Be helpful.", messages=(UserMessage(content="hi"),))
    body = context_to_openai(ctx, _COPILOT_MODEL)
    assert body["messages"][0]["role"] == "developer"
    assert "helpful" in body["messages"][0]["content"]

def test_context_to_openai_tools():
    ctx = Context(
        messages=(UserMessage(content="hi"),),
        tools=(Tool(name="read_file", description="Read", parameters={"type": "object", "properties": {}}),),
    )
    body = context_to_openai(ctx, _COPILOT_MODEL)
    assert body["tools"][0]["function"]["name"] == "read_file"
