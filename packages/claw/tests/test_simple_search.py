"""Tests for SimpleMemorySearch — keyword search over workspace files."""
import pytest
from xdog.claw.core.memory.simple_search import SimpleMemorySearch


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with MEMORY.md and daily log files."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    (ws / "MEMORY.md").write_text(
        "# User Preferences\n\n"
        "The user prefers dark mode and vim keybindings.\n\n"
        "# Technical Context\n\n"
        "The project uses Python 3.12 and asyncio for concurrency.\n",
        encoding="utf-8",
    )

    memory_dir = ws / "memory"
    memory_dir.mkdir()
    (memory_dir / "2025-01-15.md").write_text(
        "# 2025-01-15\n\n"
        "Discussed Python asyncio patterns for the queue system.\n\n"
        "Reviewed vim keybinding configuration.\n",
        encoding="utf-8",
    )

    return ws

@pytest.fixture
def searcher(workspace):
    return SimpleMemorySearch(workspace)

@pytest.mark.asyncio
async def test_search_returns_matches(searcher):
    """Keywords found in MEMORY.md and daily logs return results."""
    results = await searcher.search("dark mode")
    assert len(results) >= 1
    assert any("dark mode" in r.text.lower() for r in results)

@pytest.mark.asyncio
async def test_memory_md_scores_higher_than_daily_log(searcher):
    """MEMORY.md results (base_score=2.0) rank above daily logs (1.0)."""
    results = await searcher.search("vim keybindings")
    memory_results = [r for r in results if r.source == "MEMORY.md"]
    log_results = [r for r in results if r.source != "MEMORY.md"]
    assert len(memory_results) >= 1 and len(log_results) >= 1
    assert memory_results[0].score > log_results[0].score
