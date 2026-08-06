"""Tests for block chunker."""
from xdog.claw.core.chunker import BlockChunker


def test_splits_at_paragraph_boundary():
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    chunker = BlockChunker(min_chars=10, max_chars=30)
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2
    assert "".join(chunks).replace("\n\n", "") == text.replace("\n\n", "")

def test_never_splits_inside_code_fence():
    text = "Before.\n\n```python\ndef foo():\n    pass\n```\n\nAfter."
    chunker = BlockChunker(min_chars=5, max_chars=40)
    chunks = chunker.chunk(text)
    for chunk in chunks:
        if "```python" in chunk:
            assert "```\n" in chunk or chunk.endswith("```")

def test_respects_max_chars_with_hard_break():
    text = "a" * 200
    chunker = BlockChunker(min_chars=10, max_chars=50)
    chunks = chunker.chunk(text)
    assert all(len(c) <= 55 for c in chunks)
