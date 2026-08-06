"""Summarizer — generates structured conversation summaries via direct LLM call.

Uses the ai provider's ``complete()`` method directly instead of creating
a full Agent, since summarization is a single stateless LLM call with no
tool use.
"""
from __future__ import annotations

import logging

from xdog.agent import AgentMessage
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import (
    AssistantMessage,
    Context,
    TextContent,
    UserMessage,
)
from xdog.claw.core.compaction.prompts import SUMMARIZER_SYSTEM_PROMPT, build_summary_prompt

logger = logging.getLogger(__name__)


class Summarizer:
    """Generates a structured summary of a conversation.

    Reusable — create once per group, call ``summarize()`` each compaction.
    """

    def __init__(self, model: str) -> None:
        self._model = model

    async def summarize(
        self,
        messages: list[AgentMessage],
        previous_summary: str | None = None,
    ) -> str:
        """Produce a structured summary of the conversation.

        Returns the summary text, or a fallback string if summarization fails.
        """
        import xdog.ai as ai
        prompt_text = build_summary_prompt(previous_summary)
        try:
            runtime = ai.load()
            # `Context` carries only the three wire message kinds; a
            # CustomAgentMessage is claw's own bookkeeping and has no
            # representation to send, so it is dropped rather than passed
            # through to be rejected deeper in.
            sendable = [m for m in messages if not isinstance(m, CustomAgentMessage)]
            context = Context(
                system_prompt=SUMMARIZER_SYSTEM_PROMPT,
                messages=(*sendable, UserMessage(content=prompt_text)),
            )
            result = await runtime.complete(self._model, context)
            return _extract_text(result)
        except Exception:
            logger.exception("Failed to generate compaction summary")
            return "Conversation summary unavailable."


def _extract_text(message: AssistantMessage) -> str:
    """Extract text content from an AssistantMessage."""
    parts: list[str] = []
    for part in message.content:
        if isinstance(part, TextContent) and part.text:
            parts.append(part.text)
    return "\n\n".join(parts) if parts else "(no summary)"
