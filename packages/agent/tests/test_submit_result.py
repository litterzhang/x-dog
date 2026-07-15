"""Tests for the submit_result built-in tool (schema validation + ctx handoff)."""

import pytest
from agent.tools import create_submit_result_tool
from agent.tools.registry import registered_tool_names
from ai.types import TextContent


def _text(result) -> str:
    """Extract plain text from an AgentToolResult."""
    return "\n".join(p.text for p in result.content if isinstance(p, TextContent))


_SCHEMA = {"category": "string", "count": "integer", "active": "boolean"}


@pytest.mark.asyncio
async def test_valid_payload_accepted_and_stored_in_sink():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_output_schema": _SCHEMA, "flow_result_sink": sink}
    payload = {"category": "depin", "count": 3, "active": True}
    result = await tool.execute("c1", {"result": payload}, ctx=ctx)
    assert "accepted" in _text(result).lower()
    assert sink["result"] == payload


@pytest.mark.asyncio
async def test_missing_field_rejected_and_sink_unset():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_output_schema": _SCHEMA, "flow_result_sink": sink}
    result = await tool.execute("c1", {"result": {"category": "depin", "count": 3}}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "active" in _text(result)
    assert "result" not in sink


@pytest.mark.asyncio
async def test_wrong_type_rejected():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_output_schema": _SCHEMA, "flow_result_sink": sink}
    payload = {"category": "depin", "count": "three", "active": True}
    result = await tool.execute("c1", {"result": payload}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "count" in _text(result)
    assert "result" not in sink


@pytest.mark.asyncio
async def test_boolean_rejected_where_integer_expected():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_output_schema": {"count": "integer"}, "flow_result_sink": sink}
    result = await tool.execute("c1", {"result": {"count": True}}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "result" not in sink


@pytest.mark.asyncio
async def test_no_schema_accepts_any_object():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_result_sink": sink}
    payload = {"anything": "goes", "n": 1}
    result = await tool.execute("c1", {"result": payload}, ctx=ctx)
    assert "accepted" in _text(result).lower()
    assert sink["result"] == payload


@pytest.mark.asyncio
async def test_non_object_result_rejected():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_output_schema": _SCHEMA, "flow_result_sink": sink}
    result = await tool.execute("c1", {"result": "not an object"}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "result" not in sink


@pytest.mark.asyncio
async def test_none_ctx_does_not_crash():
    tool = create_submit_result_tool()
    result = await tool.execute("c1", {"result": {"x": 1}})
    assert "accepted" in _text(result).lower()


def test_submit_result_is_registered():
    assert "submit_result" in registered_tool_names()


def test_tool_schema_exposes_result_param():
    tool = create_submit_result_tool()
    assert tool.name == "submit_result"
    props = tool.parameters["properties"]
    assert props["result"]["type"] == "object"
    assert "result" in tool.parameters["required"]
