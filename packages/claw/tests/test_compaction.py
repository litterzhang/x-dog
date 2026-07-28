"""Tests for compaction engine."""
from claw.core.compaction import (
    compact_transcript,
    estimate_tokens,
)
from claw.core.compaction.transcript import extract_file_ops, find_cut_point


def test_estimate_tokens_includes_tool_calls():
    """Tool call arguments should be counted in token estimate."""
    turns = [
        {"content": "", "tool_calls": [{"arguments": {"path": "x" * 400}}]},
    ]
    assert estimate_tokens(turns) > 0

def test_find_cut_point():
    turns = [{"content": "x" * 100} for _ in range(10)]
    assert find_cut_point(turns, target_tokens=50) == 8
    assert find_cut_point([{"content": "short"}], target_tokens=10_000) == 0

def test_extract_file_ops():
    turns = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "filesystem", "arguments": {"action": "read", "path": "/a.py"}},
                {"name": "filesystem", "arguments": {"action": "write", "path": "/b.py"}},
            ],
        },
    ]
    result = extract_file_ops(turns)
    assert "/a.py" in result and "/b.py" in result
    assert extract_file_ops([{"role": "user", "content": "hello"}]) == ""

def test_compact_with_target_tokens():
    turns = [{"role": "user", "content": f"msg{i:02d}" + "x" * 96} for i in range(20)]
    result = compact_transcript(turns, summary="Old stuff happened", target_tokens=75)
    assert result[0]["is_compaction"] is True
    assert "Old stuff happened" in result[0]["content"]
    assert result[0]["content"].startswith("<previous-summary>")
    assert len(result) > 1

def test_compact_skips_orphaned_tool_results():
    turns = [
        {"role": "user", "content": "x" * 400},
        {"role": "tool", "content": "orphan", "tool_call_id": "tc1"},
        {"role": "user", "content": "x" * 400},
        {"role": "user", "content": "recent"},
    ]
    result = compact_transcript(turns, summary="s", target_tokens=50)
    for entry in result[1:]:
        assert entry.get("role") != "tool" or entry.get("content") != "orphan"
