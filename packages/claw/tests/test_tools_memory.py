"""Tests for memory tool."""
import pytest
from claw.core.tools.tool_memory import create_memory_tool


@pytest.mark.asyncio
async def test_memory_get_path_traversal(tmp_path):
    """Path traversal with ../  should be denied."""
    ctx = {"workspace_dir": str(tmp_path)}
    tool = create_memory_tool()
    result = await tool.execute("id", {"action": "get", "filename": "../../etc/passwd"}, None, None, ctx=ctx)
    text = result.content[0].text.lower()
    assert "access denied" in text or "not found" in text

@pytest.mark.asyncio
async def test_memory_get_prefix_attack(tmp_path):
    """Workspace name that is a prefix of a sibling directory should not grant access."""
    # Create /tmp/xxx/work (workspace) and /tmp/xxx/workspace_secret (sibling)
    base = tmp_path / "base"
    workspace = base / "work"
    workspace.mkdir(parents=True)
    sibling = base / "workspace_secret"
    sibling.mkdir(parents=True)
    (sibling / "secret.txt").write_text("top secret")

    ctx = {"workspace_dir": str(workspace)}
    tool = create_memory_tool()
    # "../workspace_secret/secret.txt" starts with workspace prefix "work" as string
    # but is_relative_to should catch it
    result = await tool.execute("id", {"action": "get", "filename": "../workspace_secret/secret.txt"}, None, None, ctx=ctx)
    text = result.content[0].text
    assert "access denied" in text.lower() or "not found" in text.lower()
    assert "top secret" not in text
