"""Tests for hybrid memory search, RRF, and temporal decay."""
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from claw.core.memory.types import MemoryChunk
from claw.core.memory.indexer import EmbeddingModel
from claw.core.memory.search import (
    HybridMemorySearch,
    reciprocal_rank_fusion,
    temporal_decay,
)


def test_temporal_decay_boosts_recent():
    now = time.time()
    old = MemoryChunk(text="old", source="log", score=1.0, timestamp=now - 86400 * 10)
    new = MemoryChunk(text="new", source="log", score=1.0, timestamp=now)
    res = temporal_decay([old, new])
    assert res[0].text == "new"
    assert res[0].score > res[1].score


def test_temporal_decay_exempts_evergreen():
    now = time.time()
    old_evergreen = MemoryChunk(
        text="ever", source="MEMORY.md", score=1.0, timestamp=now - 86400 * 100
    )
    res = temporal_decay([old_evergreen])
    assert res[0].score == 1.0


def test_reciprocal_rank_fusion():
    r1 = [MemoryChunk(text="A", source="s1"), MemoryChunk(text="B", source="s1")]
    r2 = [MemoryChunk(text="B", source="s1"), MemoryChunk(text="C", source="s1")]
    res = reciprocal_rank_fusion(r1, r2)
    assert res[0].text == "B"  # B is in both → highest RRF score


class FakeEmbedding:
    DIMENSIONS = 4
    async def encode(self, texts):
        return [[1.0, 0.0, 0.0, 0.0]] * len(texts)
    async def encode_query(self, query):
        return [1.0, 0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_hybrid_search_returns_results(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "MEMORY.md").write_text("My favorite color is blue.")

    with patch.object(EmbeddingModel, "DIMENSIONS", 4):
        hybrid = HybridMemorySearch(ws, tmp_path / "test.db", FakeEmbedding())
        results = await hybrid.search("favorite color", top_k=5)
        assert len(results) >= 1
        assert any("blue" in r.text for r in results)
        await hybrid.close()


@pytest.mark.asyncio
async def test_reindex_file_updates_search(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    memory_file = ws / "MEMORY.md"
    memory_file.write_text("initial content")

    with patch.object(EmbeddingModel, "DIMENSIONS", 4):
        hybrid = HybridMemorySearch(ws, tmp_path / "test.db", FakeEmbedding())
        await hybrid.search("initial", top_k=5)
        memory_file.write_text("initial content\n\nupdated with new facts")
        await hybrid.reindex_file(memory_file, "MEMORY.md")
        results = await hybrid.search("updated facts", top_k=5)
        assert len(results) >= 1
        assert any("updated" in r.text for r in results)
        await hybrid.close()
