"""SDK factory: create a fully-wired AgentSession.

This is the main entry point for constructing a coding agent session.
It wires together agent.Agent, ai.stream, tools, settings,
and session management.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.agent import Agent
from agent import AgentTool as CoreAgentTool

from coding.config import (
    GlobalConfig,
    ProjectConfig,
    RuntimeConfig,
    get_sessions_dir,
)
from coding.core.agent_session import AgentSession
from coding.core.defaults import DEFAULT_MODEL, DEFAULT_THINKING_LEVEL
from coding.core.session_manager import SessionData, SessionManager
from coding.core.settings_manager import SettingsManager
from coding.core.tools import get_default_tools

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateSessionOptions:
    """Options for creating an agent session."""

    working_dir: Path | None = None
    model_name: str | None = None
    thinking_level: str | None = None
    resume: bool = False
    resume_id: str | None = None
    verbose: bool = False
    config_path: Path | None = None
    overrides: dict[str, Any] | None = None


@dataclass(frozen=True)
class CreateSessionResult:
    """Result from create_agent_session."""

    session: AgentSession
    model_fallback_message: str | None = None


def create_agent_session(options: CreateSessionOptions | None = None) -> CreateSessionResult:
    """Create a fully-wired AgentSession.

    This factory:
    1. Loads global + project config
    2. Creates SessionManager and SettingsManager
    3. Creates or resumes a session
    4. Resolves the model (from options, session, settings, or defaults)
    5. Creates tools from agent built-in factories
    6. Constructs agent.Agent with stream_fn from ai provider
    7. Wraps in AgentSession for lifecycle management

    Returns
    -------
    CreateSessionResult
        The session and any model fallback warning.
    """
    opts = options or CreateSessionOptions()
    wd = (opts.working_dir or Path.cwd()).resolve()

    # Load configuration
    global_cfg = GlobalConfig.load(opts.config_path)
    project_cfg = ProjectConfig.load(wd)
    ov = opts.overrides or {}

    config = RuntimeConfig.resolve(
        global_cfg=global_cfg,
        project_cfg=project_cfg,
        working_dir=wd,
        overrides=ov,
    )

    settings = SettingsManager(project_dir=wd)
    session_mgr = SessionManager(sessions_dir=get_sessions_dir())

    # Build model catalog for default model resolution
    import ai
    provider = ai.provider("copilot")

    def _first_model_id() -> str:
        """Return the first model id from the provider, or 'sonnet' as last resort."""
        all_models = provider.models()
        return all_models[0].id if all_models else "sonnet"

    # Resolve or create session
    session_data: SessionData
    model_fallback_message: str | None = None

    if opts.resume:
        loaded = session_mgr.get_most_recent()
        if loaded is None:
            raise RuntimeError("No previous session to resume.")
        session_data = loaded
    elif opts.resume_id:
        loaded = session_mgr.load_session(opts.resume_id)
        if loaded is None:
            raise RuntimeError(f"Session not found: {opts.resume_id}")
        session_data = loaded
    else:
        model_name = ov.get("model") or config.model or DEFAULT_MODEL or _first_model_id()
        session_data = session_mgr.create_session(model=model_name)

    # Resolve active model from catalog
    model_id = ov.get("model") or session_data.model or config.model or DEFAULT_MODEL or _first_model_id()
    model = provider.model(model_id)
    if model is None:
        model_fallback_message = f"Could not resolve model: {model_id}"
        fallback_id = _first_model_id()
        model = provider.model(fallback_id)
        if model is not None:
            model_fallback_message += f". Using {model.id}"

    # Resolve thinking level
    thinking_level = ov.get("thinking_level") or config.thinking_level or DEFAULT_THINKING_LEVEL
    if model and not model.reasoning:
        thinking_level = "off"

    # Create tools from agent built-in factories
    agent_tools: list[CoreAgentTool] = get_default_tools(wd)

    # Create Agent with explicit stream_fn from provider
    from agent.helpers import stream_fn_from_provider
    from agent import AgentConfig
    from ai.types import StreamOptions

    agent = Agent(
        stream_fn_from_provider(provider),
        config=AgentConfig(
            model=model_id,
            system_prompt="",  # will be rebuilt on first prompt
            options=StreamOptions(
                thinking=thinking_level if thinking_level != "off" else None,
            ),
        ),
        tools=agent_tools,
    )

    # Restore messages if resuming
    if session_data.messages:
        agent.replace_messages(session_data.messages)

    # Create AgentSession wrapper
    session = AgentSession(
        agent=agent,
        session_data=session_data,
        session_manager=session_mgr,
        settings=settings,
        tool_registry=None,
        bash=None,
        working_dir=wd,
    )

    return CreateSessionResult(
        session=session,
        model_fallback_message=model_fallback_message,
    )
