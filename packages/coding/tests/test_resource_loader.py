"""Tests for the resource loader module."""


from coding.core.resource_loader import (
    discover_context_files,
    load_project_resources,
)


def test_discover_context_files_claude_md(tmp_path):
    """Discovers CLAUDE.md in working directory."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My Project\nInstructions here.", encoding="utf-8")

    files = discover_context_files(tmp_path)
    assert len(files) == 1
    assert files[0].relative_path == "CLAUDE.md"
    assert "Instructions here" in files[0].content
    assert files[0].path == claude_md

def test_discover_context_files_project_dir(tmp_path):
    """Discovers files inside .coding/ directory."""
    project_dir = tmp_path / ".coding"
    project_dir.mkdir()
    inst = project_dir / "INSTRUCTIONS.md"
    inst.write_text("Custom instructions", encoding="utf-8")

    files = discover_context_files(tmp_path)
    assert len(files) == 1
    assert files[0].relative_path == ".coding/INSTRUCTIONS.md"

def test_discover_context_files_skips_large(tmp_path):
    """Skips files larger than MAX_CONTEXT_FILE_SIZE."""
    large = tmp_path / "CLAUDE.md"
    # Write >64KB
    large.write_text("x" * (65 * 1024), encoding="utf-8")

    files = discover_context_files(tmp_path)
    assert len(files) == 0

def test_project_resources_to_file_entries(tmp_path):
    """to_file_entries converts to system prompt format."""
    (tmp_path / "CLAUDE.md").write_text("Hello", encoding="utf-8")

    resources = load_project_resources(tmp_path)
    entries = resources.to_file_entries()
    assert len(entries) == 1
    assert entries[0]["type"] == "file_content"
    assert entries[0]["path"] == "CLAUDE.md"
    assert entries[0]["content"] == "Hello"
