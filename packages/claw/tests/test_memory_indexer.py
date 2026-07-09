"""Tests for MemoryIndexer with real embedding and sqlite-vec support."""
import asyncio
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from claw.core.memory.indexer import (
    EmbeddingModel,
    MemoryChunk,
    MemoryIndexer,
    _cosine_similarity,
    _floats_to_blob,
    _blob_to_floats,
    get_embedding_model,
)

# ---------------------------------------------------------------------------
# Unit tests for chunking (unchanged)
# ---------------------------------------------------------------------------

def test_chunk_markdown_splits_paragraphs():
    indexer = MemoryIndexer(Path(":memory:"))
    text = "a" * 1600 + "\n\n" + "b" * 1600
    chunks = indexer.chunk_markdown(text)
    assert len(chunks) == 2
    assert chunks[0].startswith("a")
    assert chunks[1].startswith("b")

def test_chunk_markdown_merges_small_paragraphs():
    indexer = MemoryIndexer(Path(":memory:"))
    text = "p1\n\np2\n\np3"
    chunks = indexer.chunk_markdown(text)
    assert len(chunks) == 1
    assert chunks[0] == "p1\n\np2\n\np3"

# ---------------------------------------------------------------------------
# Blob helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Integration: index and retrieve with real DB (no mocks)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_and_fallback_search(tmp_path):
    """Index a file with mock embeddings and retrieve via fallback search."""
    db_path = tmp_path / "test.db"
    md_file = tmp_path / "test.md"
    md_file.write_text("hello world\n\nthis is a test document")

    # Use a fake embedding model that returns deterministic vectors
    class FakeEmbedding:
        DIMENSIONS = 4

        async def encode(self, texts):
            return [[float(i + 1)] * 4 for i, _ in enumerate(texts)]

        async def encode_query(self, query):
            return [1.0] * 4

    with patch.object(EmbeddingModel, "DIMENSIONS", 4):
        indexer = MemoryIndexer(db_path, embedding_model=FakeEmbedding())
        await indexer.index_file(md_file, "test.md")

        # The file has small content → 1 chunk
        chunks = await indexer.get_chunks([1.0] * 4, top_k=5)
        assert len(chunks) >= 1
        assert "hello world" in chunks[0].text
        assert chunks[0].source == "test.md"
        assert chunks[0].score > 0

        await indexer.close()

@pytest.mark.asyncio
async def test_incremental_indexing_skips_unchanged(tmp_path):
    """Indexing the same file twice should skip the second time."""
    db_path = tmp_path / "test.db"
    md_file = tmp_path / "data.md"
    md_file.write_text("some content")

    class FakeEmbedding:
        call_count = 0

        async def encode(self, texts):
            self.call_count += 1
            return [[0.0] * 4] * len(texts)

    fake = FakeEmbedding()
    with patch.object(EmbeddingModel, "DIMENSIONS", 4):
        indexer = MemoryIndexer(db_path, embedding_model=fake)
        await indexer.index_file(md_file, "data.md")
        assert fake.call_count == 1

        # Second call — file unchanged → should skip
        await indexer.index_file(md_file, "data.md")
        assert fake.call_count == 1

        await indexer.close()

@pytest.mark.asyncio
async def test_reindex_clears_and_rebuilds(tmp_path):
    db_path = tmp_path / "test.db"
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "MEMORY.md").write_text("long term fact")
    mem_dir = ws / "memory"
    mem_dir.mkdir()
    (mem_dir / "2025-01-01.md").write_text("daily log entry")

    with patch.object(EmbeddingModel, "DIMENSIONS", 4):
        indexer = MemoryIndexer(db_path, embedding_model=None)
        await indexer.index_file(ws / "MEMORY.md", "MEMORY.md")

        all_before = await indexer.get_all_chunks()
        assert len(all_before) == 1

        await indexer.reindex(ws)

        all_after = await indexer.get_all_chunks()
        # Should have chunks from both MEMORY.md and the daily log
        assert len(all_after) >= 2

        await indexer.close()

# ---------------------------------------------------------------------------
# Singleton model factory
# ---------------------------------------------------------------------------
