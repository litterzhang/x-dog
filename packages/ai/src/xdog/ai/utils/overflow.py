"""Context overflow detection.

Provides heuristics for estimating whether a :class:`~ai.types.Context`
is likely to exceed a model's context window, and a rough token-count
estimator for planning purposes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xdog.ai.types import Context, Model

# Rough approximation: 1 token ~ 4 characters for English text.
_CHARS_PER_TOKEN = 4


def estimate_token_count(text: str) -> int:
    """Return a rough token count estimate for *text*.

    Uses the common 4-characters-per-token heuristic.  This is intentionally
    conservative -- real tokenisers will often yield fewer tokens.
    """
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _estimate_message_tokens(msg: object) -> int:
    """Estimate the token count for a single message."""
    from xdog.ai.types import (
        AssistantMessage,
        ImageContent,
        TextContent,
        ThinkingContent,
        ToolCall,
        ToolResultMessage,
        UserMessage,
    )

    total = 0

    if isinstance(msg, UserMessage):
        if isinstance(msg.content, str):
            total += estimate_token_count(msg.content)
        else:
            for part in msg.content:
                if isinstance(part, TextContent):
                    total += estimate_token_count(part.text)
                elif isinstance(part, ImageContent):
                    # Images are typically ~768 tokens for standard resolution.
                    total += 768

    elif isinstance(msg, AssistantMessage):
        for part in msg.content:
            if isinstance(part, TextContent):
                total += estimate_token_count(part.text)
            elif isinstance(part, ThinkingContent):
                total += estimate_token_count(part.thinking)
            elif isinstance(part, ToolCall):
                import json

                total += estimate_token_count(json.dumps(part.arguments))
                total += estimate_token_count(part.name)

    elif isinstance(msg, ToolResultMessage):
        for part in msg.content:
            if isinstance(part, TextContent):
                total += estimate_token_count(part.text)
            elif isinstance(part, ImageContent):
                total += 768

    # Add a small overhead for role/structure tokens.
    total += 4
    return total


def estimate_context_tokens(context: "Context") -> int:
    """Estimate total token usage for a full :class:`Context`."""
    total = 0
    if context.system_prompt:
        total += estimate_token_count(context.system_prompt)

    for msg in context.messages:
        total += _estimate_message_tokens(msg)

    if context.tools:
        import json

        for tool in context.tools:
            total += estimate_token_count(tool.name)
            total += estimate_token_count(tool.description)
            total += estimate_token_count(json.dumps(tool.parameters))

    return total


def is_context_overflow(
    context: "Context",
    model: "Model",
    *,
    safety_margin: float = 0.95,
) -> bool:
    """Return ``True`` if *context* likely exceeds *model*'s context window.

    Parameters
    ----------
    context:
        The conversation context.
    model:
        The model whose ``context_window`` is the limit.
    safety_margin:
        Fraction of the context window to treat as the effective limit.
        Defaults to 0.95 (leave 5% headroom).
    """
    if model.context_window <= 0:
        return False  # Unknown context window -- cannot determine overflow.

    estimated = estimate_context_tokens(context)
    effective_limit = int(model.context_window * safety_margin)
    return estimated > effective_limit
