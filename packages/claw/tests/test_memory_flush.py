"""Tests for compaction prompts and triggers."""
from claw.core.compaction.prompts import build_flush_prompt, build_summary_prompt, should_compact

def test_should_compact_threshold():
    assert should_compact(10, 180_000, context_window=200_000) is True
    assert should_compact(10, 100_000, context_window=200_000) is False
    assert should_compact(200, 1000, context_window=200_000) is True
    assert should_compact(50, 1000, context_window=200_000) is False
    assert should_compact(5, 9_000, context_window=10_000) is True
    assert should_compact(5, 8_000, context_window=10_000) is False
