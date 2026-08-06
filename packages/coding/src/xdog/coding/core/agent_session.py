"""Agent session: wraps agent.Agent with session persistence, compaction, and branching."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xdog.agent import (
    AgentEndEvent,
    AgentEvent,
    AgentMessage,
    MessageEndEvent,
)
from xdog.agent.agent import Agent
from xdog.ai.types import (
    AssistantMessage,
    TextContent,
    UserMessage,
)
from xdog.coding.core.defaults import COMPACTION_THRESHOLD_RATIO, MAX_CONTEXT_TOKENS
from xdog.coding.core.event_bus import EventBus, get_event_bus
from xdog.coding.core.messages import dicts_to_messages, messages_to_dicts
from xdog.coding.core.session_manager import SessionData, SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)


@dataclass
class AgentSession:
    """Manages a coding conversation wrapping agent.Agent.

    The session owns the Agent instance, subscribes to its events for
    persistence, and provides higher-level operations like compaction
    and session branching.
    """

    agent: Agent
    session_data: SessionData
    session_manager: SessionManager
    settings: SettingsManager
    tool_registry: Any  # kept for backward compat, may be None
    bash: Any  # kept for backward compat, may be None
    working_dir: Path
    event_bus: EventBus = field(default_factory=get_event_bus)
    _unsubscribe: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # Restore session-level settings
        if self.session_data.settings:
            self.settings.load_session_settings(self.session_data.settings)

        # Subscribe to agent events for persistence and event bus forwarding
        self._unsubscribe = self.agent.subscribe(self._on_agent_event)

    # -- Properties --

    @property
    def messages(self) -> list[AgentMessage]:
        return list(self.agent.state.messages)

    @property
    def session_id(self) -> str:
        return self.session_data.session_id

    @property
    def model(self) -> str:
        return self.agent.state.model

    @property
    def is_streaming(self) -> bool:
        return self.agent.state.is_streaming

    # -- Public API --

    async def send_message(self, user_text: str) -> AssistantMessage | None:
        """Send a user message and run the full agent loop.

        Returns the final assistant message, or None if aborted.
        """
        # Rebuild system prompt before each turn
        self._rebuild_system_prompt()

        # Check for compaction before the turn
        await self._maybe_compact()

        # Run the agent loop
        event_stream = await self.agent.prompt(user_text)

        last_assistant: AssistantMessage | None = None
        async for event in event_stream:
            if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
                last_assistant = event.message

        # Persist after turn
        self._persist()

        return last_assistant

    def steer(self, message: str) -> None:
        """Queue a steering interrupt for the current turn."""
        self.agent.steer(message)

    def abort(self) -> None:
        """Cancel the current agent turn."""
        self.agent.abort()

    # -- Model / thinking --

    def set_model(self, model: str) -> None:
        """Switch the active model."""
        self.agent.set_model(model)
        self._persist()

    def set_thinking_level(self, level: str | None) -> None:
        """Switch the thinking/reasoning level."""
        from dataclasses import replace
        self.agent.set_options(replace(self.agent.options, thinking=level))
        self._persist()

    # -- Compaction --

    async def compact(self) -> None:
        """Force conversation compaction."""
        await self._run_compaction()

    async def _maybe_compact(self) -> None:
        """Check if the conversation needs compaction and run it if so."""
        messages = self.messages
        if not messages:
            return

        char_count = 0
        for msg in messages:
            if isinstance(msg, UserMessage):
                if isinstance(msg.content, str):
                    char_count += len(msg.content)
                else:
                    for part in msg.content:
                        if isinstance(part, TextContent):
                            char_count += len(part.text)
                        else:
                            char_count += 200
            elif isinstance(msg, AssistantMessage):
                for part in msg.content:
                    if isinstance(part, TextContent):
                        char_count += len(part.text)
                    else:
                        char_count += 200
            else:
                char_count += 200

        estimated_tokens = char_count / 4
        threshold = MAX_CONTEXT_TOKENS * COMPACTION_THRESHOLD_RATIO

        if estimated_tokens > threshold:
            await self._run_compaction()

    async def _run_compaction(self) -> None:
        """Execute compaction: summarize old messages and replace them."""
        from xdog.coding.core.compaction.compaction import compact_messages

        current_messages = self.messages
        if len(current_messages) < 4:
            return

        try:
            compacted = await compact_messages(current_messages, self.agent)
            self.agent.replace_messages(compacted)
            await self.event_bus.emit("compaction", message_count=len(compacted))
            self._persist()
        except Exception as exc:
            logger.warning("Compaction failed: %s", exc)

    # -- Session management --

    def clear(self) -> None:
        """Clear conversation history."""
        self.agent.clear_messages()
        self._persist()

    def switch_session(self, session_id: str) -> bool:
        """Switch to a different session. Returns True if successful."""
        loaded = self.session_manager.load_session(session_id)
        if loaded is None:
            return False

        self.session_data = loaded
        restored_messages = list(self.session_data.messages)
        self.agent.replace_messages(restored_messages)

        if self.session_data.settings:
            self.settings.load_session_settings(self.session_data.settings)

        return True

    # -- Branch management --

    def create_branch(self, *, at_index: int | None = None) -> str:
        """Create a conversation branch. Returns the branch id."""
        messages = self.messages
        branch_point = at_index if at_index is not None else len(messages) - 1
        branch_id = uuid.uuid4().hex[:8]
        branch_messages = messages_to_dicts(messages[:branch_point + 1])
        self.session_data.branches.append({
            "branch_id": branch_id,
            "branch_point": branch_point,
            "messages": branch_messages,
        })
        self._persist()
        return branch_id

    def restore_branch(self, branch_id: str) -> bool:
        """Restore a previously created branch."""
        for branch in self.session_data.branches:
            if branch["branch_id"] == branch_id:
                restored = dicts_to_messages(branch["messages"])
                self.agent.replace_messages(restored)
                self._persist()
                return True
        return False

    # -- Internal --

    def _rebuild_system_prompt(self) -> None:
        """Rebuild and set the system prompt from config and tools."""
        from xdog.coding.config import PlatformInfo, RuntimeConfig

        # Get tool definitions from the agent's current tools
        tool_defs = [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self.agent.state.tools
        ]

        # Build a minimal RuntimeConfig for system prompt generation
        prompt = build_system_prompt(
            RuntimeConfig(
                model=self.agent.state.model.id if self.agent.state.model else "unknown",
                thinking_level=str(self.agent.state.thinking_level or "normal"),
                allowed_tools=tuple(t.name for t in self.agent.state.tools),
                custom_instructions=self.settings.custom_instructions,
                extensions=(),
                working_dir=str(self.working_dir),
                platform_info=PlatformInfo.detect(),
            ),
            tool_defs,
        )
        self.agent.set_system_prompt(prompt)

    def _on_agent_event(self, event: AgentEvent) -> None:
        """Handle agent lifecycle events."""
        if isinstance(event, AgentEndEvent):
            self._persist()

    def _persist(self) -> None:
        """Save session state to disk."""
        self.session_data.messages = self.messages
        self.session_data.settings = self.settings.session_to_dict()
        self.session_data.model = self.agent.state.model or ""

        # Update summary from last assistant message
        msgs = self.messages
        if msgs:
            last = msgs[-1]
            if isinstance(last, AssistantMessage):
                for part in last.content:
                    if isinstance(part, TextContent):
                        self.session_data.summary = part.text[:120]
                        break

        self.session_manager.save_session(self.session_data)

    def dispose(self) -> None:
        """Clean up resources."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
