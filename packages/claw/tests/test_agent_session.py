"""Tests for AgentSession — agent turn execution, persistence, tools."""
import asyncio
import pytest
from pathlib import Path
from ai.types import (
    AssistantMessage, Context, DoneEvent, StartEvent,
    TextContent, ToolCall, ToolCallDoneEvent, UserMessage,
)
from ai.utils.event_stream import EventStream
from agent import (
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
)
from claw.core.runtime.session import (
    AgentSession, TurnResult,
)
from claw.core.persistence.transcript_convert import (
    extract_final_text,
)
from claw.core.compaction.transcript import (
    extract_previous_summary as _extract_previous_summary,
)
from claw.core.persistence.transcript_store import TranscriptStore
from claw.core.runtime.group import GroupRuntime
from claw.core.prompt import init_workspace
from claw.core.types import Group, UserInput
from agent.tools import create_filesystem_tool

# ---------------------------------------------------------------------------
# Mock stream_fn helpers
# ---------------------------------------------------------------------------

def make_stream_fn(response_text="", tool_calls=None):
    def stream_fn(model_id, context, options=None):
        parts = []
        if response_text:
            parts.append(TextContent(text=response_text))
        if tool_calls:
            for tc in tool_calls:
                parts.append(ToolCall(
                    id=tc["id"], name=tc["name"],
                    arguments=tc.get("arguments", {}),
                ))
        msg = AssistantMessage(content=tuple(parts))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    return stream_fn

def make_multi_round_stream_fn(rounds):
    call_count = [0]

    def stream_fn(model_id, context, options=None):
        idx = min(call_count[0], len(rounds) - 1)
        call_count[0] += 1
        text, tcs = rounds[idx]
        parts = []
        if text:
            parts.append(TextContent(text=text))
        if tcs:
            for tc in tcs:
                parts.append(ToolCall(
                    id=tc["id"], name=tc["name"],
                    arguments=tc.get("arguments", {}),
                ))
        msg = AssistantMessage(content=tuple(parts))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    stream_fn._call_count = call_count
    return stream_fn

def make_capturing_stream_fn(response_text="ok"):
    captured = {"model_id": None, "context": None, "options": None}

    def stream_fn(model_id, context, options=None):
        captured["model_id"] = model_id
        captured["context"] = context
        captured["options"] = options
        if hasattr(context, 'system_prompt'):
            captured["system_prompt"] = context.system_prompt
        msg = AssistantMessage(content=(TextContent(text=response_text),))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    stream_fn.captured = captured
    return stream_fn

def make_failing_stream_fn(error_msg="LLM error"):
    def stream_fn(model_id, context, options=None):
        async def _gen():
            raise RuntimeError(error_msg)
            yield  # noqa: E501

        fut = asyncio.get_running_loop().create_future()
        return EventStream.from_async_generator(_gen(), result_future=fut)

    return stream_fn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime(ws, tmp_path, *, stream_fn=None, tools=None, group_id="g1"):
    """Create a GroupRuntime for testing."""
    store = TranscriptStore(tmp_path / "sessions")
    return GroupRuntime(
        group=Group(id=group_id, name=group_id),
        data_dir=tmp_path,
        model="test/dummy",
        stream_fn=stream_fn,
        workspace_dir=ws,
        transcript_store=store,
    )

def _make_session(ws, tmp_path, *, stream_fn=None, tools=None, group_id="g1"):
    """Create an AgentSession via GroupRuntime."""
    runtime = _make_runtime(ws, tmp_path, stream_fn=stream_fn, tools=tools, group_id=group_id)
    # Override tools if provided
    if tools:
        runtime._tools = tools
    return runtime.get_or_create_session()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def setup(tmp_path):
    ws = tmp_path / "workspace"
    init_workspace(ws, agent_name="TestBot")
    return ws, tmp_path

# ---------------------------------------------------------------------------
# Core turn lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_turn_returns_response(setup):
    ws, tmp_path = setup
    session = _make_session(ws, tmp_path, stream_fn=make_stream_fn("Hello! I'm TestBot."))
    result = await session.run_turn(UserInput(group_id="g1", content="hi", sender="user"))
    assert result.response_text == "Hello! I'm TestBot."
    assert result.error is None

@pytest.mark.asyncio
async def test_run_turn_persists_transcript(setup):
    ws, tmp_path = setup
    runtime = _make_runtime(ws, tmp_path, stream_fn=make_stream_fn("response"))
    session = runtime.get_or_create_session()
    await session.run_turn(UserInput(group_id="g1", content="hello", sender="user"))
    meta = runtime.transcript_store.get_active_session("g1")
    transcript = runtime.transcript_store.load_transcript(meta.session_id)
    assert len(transcript) == 2  # user + assistant
    assert transcript[0]["role"] == "user"
    assert transcript[1]["role"] == "assistant"

@pytest.mark.asyncio
async def test_run_turn_includes_system_prompt(setup):
    ws, tmp_path = setup
    sfn = make_capturing_stream_fn("ok")
    session = _make_session(ws, tmp_path, stream_fn=sfn)
    await session.run_turn(UserInput(group_id="g1", content="hi", sender="user"))
    from ai.types import system_prompt_text
    prompt_text = system_prompt_text(sfn.captured["system_prompt"]) or ""
    assert "TestBot" in prompt_text

# ---------------------------------------------------------------------------
# Tool call loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_call_loop(setup):
    """Agent requests tool -> executes -> feeds result -> Agent responds."""
    ws, tmp_path = setup
    (ws / "hello.txt").write_text("Hello from file!")

    sfn = make_multi_round_stream_fn([
        ("", [{"id": "call_1", "name": "filesystem", "arguments": {"action": "read", "path": str(ws / "hello.txt")}}]),
        ("The file says: Hello from file!", None),
    ])

    tools = [create_filesystem_tool()]
    session = _make_session(ws, tmp_path, stream_fn=sfn, tools=tools)
    result = await session.run_turn(UserInput(group_id="g1", content="read hello.txt", sender="user"))

    assert result.error is None
    assert "Hello from file!" in result.response_text
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "filesystem"

@pytest.mark.asyncio
async def test_write_file_tool(setup):
    """write_file tool creates a file in workspace."""
    ws, tmp_path = setup

    sfn = make_multi_round_stream_fn([
        ("", [{"id": "c1", "name": "filesystem", "arguments": {"action": "write", "path": str(ws / "output.txt"), "content": "written by agent"}}]),
        ("File written successfully.", None),
    ])

    tools = [create_filesystem_tool()]
    session = _make_session(ws, tmp_path, stream_fn=sfn, tools=tools)
    result = await session.run_turn(UserInput(group_id="g1", content="write file", sender="user"))

    assert result.error is None
    assert (ws / "output.txt").read_text() == "written by agent"

# ---------------------------------------------------------------------------
# _extract_previous_summary
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# extract_final_text
# ---------------------------------------------------------------------------
