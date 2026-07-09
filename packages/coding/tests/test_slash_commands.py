"""Tests for slash commands module."""

import pytest

from agent import AgentConfig
from ai.types import AssistantMessage as _AM
from ai.utils.event_stream import EventStream as _ES

def _noop_stream_fn(m, c, o): return _ES.empty(_AM(content=()))

from coding.core.slash_commands import (
    CommandResult,
    BUILTIN_COMMANDS,
    execute_command,
    list_commands,
    parse_slash_command,
)

def test_parse_slash_command():
    assert parse_slash_command("/help") == ("help", "")
    assert parse_slash_command("/model sonnet") == ("model", "sonnet")
    assert parse_slash_command("/thinking high") == ("thinking", "high")
    assert parse_slash_command("hello") is None
    assert parse_slash_command("") is None

@pytest.mark.asyncio
async def test_cmd_quit(agent_session):
    result = await execute_command("quit", "", agent_session)
    assert result.exit_requested
    assert "Goodbye" in result.output

@pytest.mark.asyncio
async def test_cmd_model_switch(agent_session):
    """Test switching model via /model <name>."""
    result = await execute_command("model", "opus", agent_session)
    assert "Switched to model" in result.output
    assert "opus" in result.output

@pytest.mark.asyncio
async def test_cmd_clear(agent_session):
    result = await execute_command("clear", "", agent_session)
    assert "cleared" in result.output.lower()

@pytest.mark.asyncio
async def test_cmd_fork(agent_session):
    # Add some messages first
    from ai.types import AssistantMessage, TextContent, UserMessage
    msgs = [
        UserMessage(content="Hello"),
        AssistantMessage(content=(TextContent(text="Hi"),)),
    ]
    agent_session.agent.replace_messages(msgs)
    agent_session.session_data.messages = list(msgs)

    result = await execute_command("fork", "", agent_session)
    assert "Branch created" in result.output

@pytest.mark.asyncio
async def test_cmd_compact(agent_session):
    result = await execute_command("compact", "", agent_session)
    assert "Compacted" in result.output

# -- Fixtures --

@pytest.fixture
def agent_session(tmp_path):
    """Create a minimal AgentSession for testing."""
    from agent.agent import Agent
    from coding.core.agent_session import AgentSession
    from coding.core.session_manager import SessionManager
    from coding.core.settings_manager import SettingsManager

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_mgr = SessionManager(sessions_dir=session_dir)
    session_data = session_mgr.create_session(model="test")

    agent = Agent(_noop_stream_fn, config=AgentConfig(system_prompt="test"))

    return AgentSession(
        agent=agent,
        session_data=session_data,
        session_manager=session_mgr,
        settings=SettingsManager(),
        tool_registry=None,
        bash=None,
        working_dir=tmp_path,
    )

@pytest.fixture
def agent_session_with_models(tmp_path):
    """Create an AgentSession with a model name."""
    from agent.agent import Agent
    from coding.core.agent_session import AgentSession
    from coding.core.session_manager import SessionManager
    from coding.core.settings_manager import SettingsManager

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_mgr = SessionManager(sessions_dir=session_dir)
    session_data = session_mgr.create_session(model="sonnet")

    agent = Agent(_noop_stream_fn, config=AgentConfig(system_prompt="test", model="sonnet"))

    return AgentSession(
        agent=agent,
        session_data=session_data,
        session_manager=session_mgr,
        settings=SettingsManager(),
        tool_registry=None,
        bash=None,
        working_dir=tmp_path,
    )
