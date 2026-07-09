"""Compaction prompts and trigger logic.

All prompts and constants for the compaction pipeline live here:
- Trigger threshold (``should_compact``)
- Flush turn prompt (``build_flush_prompt``)
- Summary prompt + system prompt (``build_summary_prompt``, ``SUMMARIZER_SYSTEM_PROMPT``)
"""
from __future__ import annotations

SUMMARIZER_SYSTEM_PROMPT = (
    "You are a conversation summarizer. "
    "Produce a structured summary of the conversation."
)

_STRUCTURED_SUMMARY_FORMAT = """\
Summarize this conversation using this EXACT format.
Do NOT include any XML tags (like <previous-summary>) in your output.

# Goal
(The primary objective the user is trying to achieve)

# Progress
(What has been accomplished so far, specific files read or modified)

# Decisions
(Key technical decisions or architectural choices made)

# Context
(Relevant state, variables, or constraints to preserve)"""


def should_compact(
    turn_count: int,
    token_estimate: int,
    context_window: int = 200_000,
) -> bool:
    """Return True when the session should trigger compaction.

    Fires at 90% of context_window, or after 200 turns.
    """
    token_threshold = int(context_window * 0.9)
    return token_estimate >= token_threshold or turn_count >= 200


def build_flush_prompt() -> str:
    """Prompt for the pre-compaction memory-write turn."""
    return (
        "Before this conversation is compacted, review what happened "
        "and save any important information:\n"
        "1. Write durable facts, preferences, and decisions to MEMORY.md "
        "using the memory tool (action: write, target: memory)\n"
        "2. Write today's observations and context to the daily log "
        "using the memory tool (action: write, target: daily)\n"
        "3. Focus on information that would be useful in future "
        "conversations\n"
        "Do not respond to the user \u2014 this is a silent maintenance turn."
    )


def build_summary_prompt(previous_summary: str | None = None) -> str:
    """Prompt for the structured summary generation.

    When *previous_summary* is provided the model is instructed to
    incorporate and update it, enabling iterative accumulation.
    """
    parts: list[str] = []
    if previous_summary:
        parts.append(
            "Incorporate and update information from the previous summary:\n"
            f"<previous-summary>\n{previous_summary}\n</previous-summary>\n"
        )
    parts.append(_STRUCTURED_SUMMARY_FORMAT)
    return "\n".join(parts)
