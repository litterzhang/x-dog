"""Tests for flow.tools — ToolRegistry and built-in demo tools."""

from __future__ import annotations

import pytest
from flow.errors import WorkflowValidationError
from flow.tools import default_registry


def test_default_registry_has_echo() -> None:
    reg = default_registry()
    tool = reg.get("echo")
    assert tool.name == "echo"


def test_get_unknown_raises() -> None:
    reg = default_registry()
    with pytest.raises(WorkflowValidationError, match="nope"):
        reg.get("nope")


def test_resolve_returns_tuple() -> None:
    reg = default_registry()
    result = reg.resolve(["echo"])
    assert len(result) == 1
    assert result[0].name == "echo"


async def test_echo_execute() -> None:
    reg = default_registry()
    tool = reg.get("echo")
    assert tool.execute is not None
    result = await tool.execute("id1", {"text": "hello"})
    assert result.content[0].text == "hello"  # type: ignore[union-attr]
