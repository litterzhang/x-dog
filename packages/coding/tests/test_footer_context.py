"""Tests for the interactive-mode footer context accounting."""

from xdog.ai.types import AssistantMessage, Usage, UserMessage
from xdog.coding.modes.interactive.interactive_mode import _context_tokens_from_messages


def test_context_tokens_uses_latest_assistant_usage():
    messages = [
        AssistantMessage(usage=Usage(input=10, output=2, cache_read=5, cache_write=1)),
        UserMessage(content="next"),
        AssistantMessage(usage=Usage(input=100, output=20, cache_read=900, cache_write=50)),
    ]

    assert _context_tokens_from_messages(messages) == 1050


def test_context_tokens_zero_without_assistant_usage():
    assert _context_tokens_from_messages([UserMessage(content="hi")]) == 0
