"""Tests for AgentSession wrapping agent.Agent."""

import pytest
from agent import AgentConfig
from ai.types import AssistantMessage as _AM
from ai.utils.event_stream import EventStream as _ES

def _noop_stream_fn(m, c, o): return _ES.empty(_AM(content=()))
from pathlib import Path
from unittest.mock import MagicMock

from agent.agent import Agent
from agent import AgentTool as CoreAgentTool, AgentToolResult
from ai.types import (
    AssistantMessage,
    Model,
    TextContent,
    UserMessage,
)

from coding.core.agent_session import AgentSession
from coding.core.messages import messages_to_dicts, dicts_to_messages
from coding.core.session_manager import SessionData, SessionManager
from coding.core.settings_manager import SettingsManager


@pytest.fixture
def tmp_session_dir(tmp_path):
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def session_mgr(tmp_session_dir):
    return SessionManager(sessions_dir=tmp_session_dir)


def test_agent_session_branching(session_mgr, tmp_path):
    session_data = session_mgr.create_session(model="test")

    agent = Agent(_noop_stream_fn, config=AgentConfig(system_prompt="test"))
    msgs = [
        UserMessage(content="Hello"),
        AssistantMessage(content=(TextContent(text="Hi there"),)),
        UserMessage(content="How are you?"),
        AssistantMessage(content=(TextContent(text="Good"),)),
    ]
    agent.replace_messages(msgs)
    session_data.messages = list(msgs)

    session = AgentSession(
        agent=agent,
        session_data=session_data,
        session_manager=session_mgr,
        settings=SettingsManager(),
        tool_registry=None,
        bash=None,
        working_dir=tmp_path,
    )

    # Create branch at index 1 (after first assistant response)
    branch_id = session.create_branch(at_index=1)
    assert branch_id is not None
    assert len(session.session_data.branches) == 1

    # Restore the branch
    assert session.restore_branch(branch_id) is True
    assert len(session.messages) == 2

    # Nonexistent branch
    assert session.restore_branch("fake") is False
