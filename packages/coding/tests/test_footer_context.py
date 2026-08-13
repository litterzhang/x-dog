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


def test_interactive_footer_uses_usable_prompt_limit():
    from unittest.mock import MagicMock

    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    mode = object.__new__(InteractiveMode)
    mode._session = MagicMock()
    mode._session.model = "gpt-5.6-sol"
    mode._session.agent.options.thinking = "high"
    mode._session.permissions.mode = "ask"
    mode._session.session_id = "session-id"
    mode._session.messages = []
    mode._session.working_dir = "/workspace/project"
    mode._session.context_window = 1_050_000
    mode._session.context_limit = 922_000
    mode._footer = MagicMock()

    mode._update_footer()

    assert mode._footer.update.call_args.kwargs["max_context"] == 922_000
