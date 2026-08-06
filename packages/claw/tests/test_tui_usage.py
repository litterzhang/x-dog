"""Tests for the TUI footer's token accounting helpers."""

from xdog.claw.channels.tui.tui_app import _context_usage_tokens


def test_context_usage_includes_cache_buckets():
    """Cached prefix tokens occupy the context window and must be counted."""
    last_turn = {"input": 100, "output": 20, "cache_read": 900, "cache_write": 50}
    session_total = {"input": 7, "output": 3, "cache_read": 0, "cache_write": 0}

    assert _context_usage_tokens(last_turn, session_total) == 1050


def test_context_usage_falls_back_to_session_input():
    """Before the first turn completes, fall back to the session input total."""
    last_turn = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    session_total = {"input": 7, "output": 3, "cache_read": 0, "cache_write": 0}

    assert _context_usage_tokens(last_turn, session_total) == 7
