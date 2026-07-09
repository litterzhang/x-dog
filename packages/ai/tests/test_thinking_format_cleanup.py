"""Tests verifying thinking_format cleanup in openai_completions.py and types.py."""

from ai.types import OpenAICompletionsCompat


def test_thinking_format_removed():
    """thinking_format field no longer exists; other compat fields remain."""
    compat = OpenAICompletionsCompat()
    assert not hasattr(compat, "thinking_format")
    # Core compat fields still present
    assert hasattr(compat, "supports_store")
    assert hasattr(compat, "supports_reasoning_effort")
    assert hasattr(compat, "max_tokens_field")
