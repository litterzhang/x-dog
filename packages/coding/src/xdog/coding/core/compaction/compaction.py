"""Compaction logic: summarize conversation history to free context space."""

from __future__ import annotations

from typing import Any

from xdog.agent import AgentMessage
from xdog.agent.agent import Agent
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.coding.core.compaction.utils import split_messages
from xdog.coding.core.defaults import COMPACTION_TARGET_RATIO, MAX_CONTEXT_TOKENS
from xdog.coding.core.prompt_templates import COMPACTION_SUMMARY_TEMPLATE

SUMMARIZE_SYSTEM_PROMPT = """\
You are a conversation summarizer. You will receive a portion of a conversation \
between a user and a coding assistant. Produce a concise summary that captures:

1. What tasks were discussed and their current status.
2. Key decisions made.
3. Important code changes or file paths mentioned.
4. Any outstanding issues or next steps.

Be concise but ensure no critical context is lost. Output only the summary text.
"""


async def compact_messages(
    messages: list[AgentMessage],
    agent: Agent,
) -> list[AgentMessage]:
    """Compact the conversation by summarizing older messages.

    Keeps recent messages intact and summarizes older ones into a single
    summary message. Returns a new (shorter) message list.
    """
    target_tokens = int(MAX_CONTEXT_TOKENS * COMPACTION_TARGET_RATIO)

    # Split into "old" (to summarize) and "recent" (to keep)
    old_messages, recent_messages = split_messages(messages, target_tokens)

    if not old_messages:
        return list(messages)

    # Build text representation of old messages for summarization
    text_parts: list[str] = []
    for msg in old_messages:
        if isinstance(msg, UserMessage):
            text = msg.content if isinstance(msg.content, str) else _extract_text(msg)
            if text:
                text_parts.append(f"[USER]: {text}")
        elif isinstance(msg, AssistantMessage):
            text = _extract_text(msg)
            if text:
                text_parts.append(f"[ASSISTANT]: {text}")

    conversation_text = "\n\n".join(text_parts)

    # Use the agent's stream_fn to summarize
    summary = await _summarize(conversation_text, agent)

    # Build the compacted history
    summary_text = COMPACTION_SUMMARY_TEMPLATE.format(summary=summary)
    summary_msg = UserMessage(content=summary_text)

    return [summary_msg] + recent_messages


def _extract_text(msg: Any) -> str:
    """Extract concatenated text from a message's content parts."""
    parts: list[str] = []
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    for part in content:
        if isinstance(part, TextContent):
            parts.append(part.text)
    return "\n".join(parts)


async def _summarize(conversation_text: str, agent: Agent) -> str:
    """Use the agent's model to summarize conversation text."""
    import xdog.ai as ai
    from xdog.ai.types import Context
    from xdog.ai.types import UserMessage as AiUserMessage

    model = agent.state.model
    if not model:
        return conversation_text[:2000]

    context = Context(
        system_prompt=SUMMARIZE_SYSTEM_PROMPT,
        messages=(AiUserMessage(content=conversation_text),),
        tools=(),
    )

    runtime = ai.load()
    result = await runtime.complete(model, context)

    text_parts: list[str] = []
    for part in result.content:
        if isinstance(part, TextContent):
            text_parts.append(part.text)

    return "\n".join(text_parts) if text_parts else conversation_text[:2000]
