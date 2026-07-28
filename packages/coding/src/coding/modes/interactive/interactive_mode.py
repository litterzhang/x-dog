"""Interactive mode: TUI-based coding agent interface.

Uses tui.TUI as the rendering engine and subscribes to agent.Agent
events for streaming responses, tool execution display, and session
management.

Component tree:
    TUI
    └── root (Container)
        ├── header (Text)
        ├── chat_log (ChatLog)
        ├── status_container (Container)
        ├── footer (FooterComponent)
        └── editor (CustomEditorComponent)
"""

from __future__ import annotations

import asyncio
import logging
import queue
import random
import re
import threading
import time
from typing import Any

from agent import (
    AgentEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from ai.types import (
    AssistantMessage,
    TextContent,
    UserMessage,
)
from tui.components.loader import Loader
from tui.components.text import Text
from tui.tui import TUI, Container

from coding.core.agent_session import AgentSession
from coding.core.slash_commands import execute_command, parse_slash_command
from coding.modes.interactive.components.chat_log import ChatLog
from coding.modes.interactive.components.custom_editor import CustomEditorComponent
from coding.modes.interactive.components.footer import FooterComponent
from coding.modes.interactive.theme import create_default_theme

logger = logging.getLogger(__name__)

# Waiting phrases for the shimmer animation
WAITING_PHRASES = [
    "pondering",
    "conjuring",
    "reasoning",
    "analyzing",
    "thinking",
    "crafting",
    "exploring",
    "synthesizing",
    "contemplating",
    "processing",
]


class InteractiveMode:
    """Main interactive TUI application for the coding agent.

    Wraps AgentSession and provides a full terminal interface with:
    - Streaming message display
    - Tool execution visualization
    - Slash command handling
    - Session management
    - Ctrl+C double-press exit logic
    """

    def __init__(self, session: AgentSession, *, verbose: bool = False) -> None:
        self._session = session
        self._verbose = verbose
        self._theme = create_default_theme()

        # State
        self._exit_requested = False
        self._last_ctrl_c_at = 0.0
        self._is_busy = False
        self._busy_started: float | None = None
        self._streaming_text = ""
        self._stream_id = "default"
        self._last_tool_component: Any = None

        # Event queue for thread-safe UI updates from agent events
        self._event_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        # Build component tree
        self._tui = TUI()

        self._header = Text("", 1, 0)
        self._chat_log = ChatLog(self._theme)
        self._status_container = Container()
        self._footer = FooterComponent(self._theme)
        self._editor = CustomEditorComponent(self._theme)

        # Status components
        self._status_text: Text | None = Text(self._theme.dim("ready"), 1, 0)
        self._status_loader: Loader | None = None
        self._status_container.add_child(self._status_text)

        root = Container()
        root.add_child(self._header)
        root.add_child(self._chat_log)
        root.add_child(self._status_container)
        root.add_child(self._footer)
        root.add_child(self._editor)

        self._tui.add_child(root)
        self._tui.set_focus(self._editor)

        # Wire editor callbacks
        self._editor.on_submit = self._handle_submit
        self._editor.on_ctrl_c = self._handle_ctrl_c
        self._editor.on_ctrl_d = self._request_exit
        self._editor.on_escape = self._handle_escape

        # Subscribe to agent events
        self._unsubscribe = self._session.agent.subscribe(self._on_agent_event)

    def run(self) -> None:
        """Start the interactive TUI (blocking)."""
        self._update_header()
        self._update_footer()

        model_name = self._session.model.id if self._session.model else "unknown"
        thinking = self._session.agent.state.thinking_level or "off"
        self._chat_log.add_system(
            f"session:{self._session.session_id[:8]} | {model_name} | thinking:{thinking}"
        )

        # Replay existing messages
        self._replay_history()

        # Register tick callback for polling events
        self._tui.on_tick(self._poll)

        try:
            self._tui.start()
        finally:
            if self._unsubscribe:
                self._unsubscribe()

    def _replay_history(self) -> None:
        """Replay existing messages from the session."""
        for msg in self._session.messages:
            if isinstance(msg, UserMessage):
                text = msg.content if isinstance(msg.content, str) else ""
                if text:
                    self._chat_log.add_user(text)
                    self._editor.add_to_history(text)
            elif isinstance(msg, AssistantMessage):
                parts: list[str] = []
                for part in msg.content:
                    if isinstance(part, TextContent):
                        parts.append(part.text)
                if parts:
                    self._chat_log.add_assistant("".join(parts))

    # -- Header / Footer --

    def _update_header(self) -> None:
        model_name = self._session.model.id if self._session.model else "unknown"
        self._header.set_text(
            self._theme.header(f"  coding | {model_name}")
        )

    def _update_footer(self) -> None:
        model_name = self._session.model.id if self._session.model else "unknown"
        thinking = self._session.agent.state.thinking_level or "off"
        self._footer.update(
            model=model_name,
            session_id=self._session.session_id,
            message_count=len(self._session.messages),
            thinking=str(thinking),
            working_dir=str(self._session.working_dir),
        )

    # -- Status management --

    def _set_status_text(self, text: str) -> None:
        """Show a static status message."""
        if self._status_loader is not None:
            self._status_loader.stop()
            self._status_loader = None
        self._status_container.clear()
        self._status_text = Text(self._theme.dim(text), 1, 0)
        self._status_container.add_child(self._status_text)

    def _set_status_busy(self, message: str = "thinking...") -> None:
        """Show an animated loader status."""
        if self._status_loader is not None:
            self._status_loader.set_message(message)
            return
        self._status_container.clear()
        self._status_text = None
        self._status_loader = Loader(
            self._tui,
            lambda s: self._theme.accent(s),
            lambda t: self._theme.accent_soft(t),
            message,
        )
        self._status_container.add_child(self._status_loader)

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        if busy:
            self._busy_started = time.time()
            phrase = random.choice(WAITING_PHRASES)
            self._set_status_busy(f"{phrase}...")
        else:
            self._busy_started = None
            self._set_status_text("ready")

    def _format_elapsed(self) -> str:
        if self._busy_started is None:
            return "0s"
        total = int(time.time() - self._busy_started)
        if total < 60:
            return f"{total}s"
        m, s = divmod(total, 60)
        return f"{m}m {s}s"

    # -- Input handling --

    def _handle_submit(self, text: str) -> None:
        """Handle editor submission."""
        value = text.strip()
        if not value:
            return

        self._editor.add_to_history(value)

        # Check for slash commands
        parsed = parse_slash_command(value)
        if parsed is not None:
            cmd, args = parsed
            self._handle_slash_command(cmd, args)
            return

        # Regular message
        self._chat_log.add_user(value)
        self._set_busy(True)
        self._streaming_text = ""

        # Run agent in background thread
        thread = threading.Thread(
            target=self._run_agent_turn,
            args=(value,),
            daemon=True,
        )
        thread.start()

    def _handle_slash_command(self, cmd: str, args: str) -> None:
        """Handle a slash command."""
        if cmd in ("quit", "exit"):
            self._request_exit()
            return

        # Run async commands in a new event loop
        def _run() -> None:
            result = asyncio.run(execute_command(cmd, args, self._session))
            self._event_queue.put({
                "type": "command_result",
                "output": result.output,
                "exit": result.exit_requested,
            })

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _handle_ctrl_c(self) -> None:
        """Ctrl+C: clear input → warn → exit on double-press."""
        now = time.time()
        has_input = len(self._editor.get_text().strip()) > 0

        if has_input:
            self._editor.set_text("")
            self._set_status_text("input cleared; press ctrl+c again to exit")
            self._last_ctrl_c_at = now
            self._tui.request_render()
            return

        if self._is_busy:
            self._session.abort()
            self._set_busy(False)
            self._chat_log.add_system("aborted")
            self._tui.request_render()
            self._last_ctrl_c_at = now
            return

        if now - self._last_ctrl_c_at <= 1.0:
            self._request_exit()
        else:
            self._set_status_text("press ctrl+c again to exit")
            self._last_ctrl_c_at = now
            self._tui.request_render()

    def _handle_escape(self) -> None:
        """Escape: abort the current request."""
        if self._is_busy:
            self._session.abort()
            self._set_busy(False)
            self._chat_log.drop_assistant(self._stream_id)
            self._chat_log.add_system("aborted")
            self._tui.request_render()

    def _request_exit(self) -> None:
        """Request clean exit."""
        if self._exit_requested:
            return
        self._exit_requested = True
        self._session.dispose()
        self._tui.stop()

    # -- Agent execution --

    def _run_agent_turn(self, message: str) -> None:
        """Run an agent turn in a background thread."""
        try:
            asyncio.run(self._async_agent_turn(message))
        except Exception as exc:
            self._event_queue.put({
                "type": "error",
                "message": str(exc),
            })

    async def _async_agent_turn(self, message: str) -> None:
        """Run the full agent turn."""
        self._session._rebuild_system_prompt()
        await self._session._maybe_compact()

        event_stream = await self._session.agent.prompt(message)

        async for event in event_stream:
            self._dispatch_agent_event(event)

        self._session._persist()
        self._event_queue.put({"type": "turn_end"})

    def _dispatch_agent_event(self, event: AgentEvent) -> None:
        """Convert agent events to UI update events (thread-safe)."""
        if isinstance(event, MessageUpdateEvent):
            msg = event.message
            if isinstance(msg, AssistantMessage):
                text_parts: list[str] = []
                for part in msg.content:
                    if isinstance(part, TextContent):
                        text_parts.append(part.text)
                if text_parts:
                    self._event_queue.put({
                        "type": "text_update",
                        "text": "".join(text_parts),
                    })

        elif isinstance(event, ToolExecutionStartEvent):
            self._event_queue.put({
                "type": "tool_call",
                "name": event.tool_name,
                "arguments": event.args,
            })

        elif isinstance(event, ToolExecutionUpdateEvent):
            if event.partial_result is not None:
                text_parts: list[str] = []
                for part in event.partial_result.content:
                    if isinstance(part, TextContent):
                        text_parts.append(part.text)
                if text_parts:
                    self._event_queue.put({
                        "type": "tool_update",
                        "name": event.tool_name,
                        "text": "".join(text_parts),
                    })

        elif isinstance(event, ToolExecutionEndEvent):
            result_text = ""
            if event.result is not None:
                # Extract text content from the result
                for part in event.result.content:
                    if isinstance(part, TextContent):
                        result_text = part.text
                        break
            # Detect errors
            is_error = result_text.startswith("Error:")
            # Pass full result for edit diffs, truncate others
            is_edit = event.tool_name == "filesystem" and _is_diff_result(result_text)
            if not is_edit:
                result_text = result_text[:500]
            self._event_queue.put({
                "type": "tool_result",
                "name": event.tool_name,
                "result": result_text,
                "is_error": is_error,
            })

    # -- Agent event subscription (runs in agent thread) --

    def _on_agent_event(self, event: AgentEvent) -> None:
        """Handle agent lifecycle events (called from agent thread)."""
        # This is the subscription handler — we don't need to duplicate
        # what _dispatch_agent_event does since we handle events in
        # _async_agent_turn's event stream iteration.
        pass

    # -- Polling / UI update --

    def _poll(self) -> None:
        """Poll the event queue and update the UI (called per frame)."""
        changed = False

        while True:
            try:
                event = self._event_queue.get_nowait()
                self._handle_ui_event(event)
                changed = True
            except queue.Empty:
                break

        if self._is_busy:
            elapsed = self._format_elapsed()
            if self._status_loader:
                self._status_loader.set_message(f"thinking... • {elapsed}")
            changed = True

        if changed:
            self._tui.request_render()

    def _handle_ui_event(self, event: dict[str, Any]) -> None:
        """Handle a UI event from the event queue."""
        event_type = event.get("type")

        if event_type == "text_update":
            text = event.get("text", "")
            self._streaming_text = text
            self._chat_log.update_assistant(text, self._stream_id)

        elif event_type == "tool_call":
            name = event.get("name", "")
            arguments = event.get("arguments", {})
            self._chat_log.add_tool(name, arguments)
            self._last_tool_component = self._chat_log.get_last_tool()

        elif event_type == "tool_update":
            # Live streaming output from bash
            if self._last_tool_component is not None:
                text = event.get("text", "")
                self._last_tool_component.set_streaming(text)

        elif event_type == "tool_result":
            result = event.get("result", "")
            is_error = event.get("is_error", False)
            if result and self._last_tool_component is not None:
                self._last_tool_component.set_result(result, is_error=is_error)
                self._last_tool_component = None

        elif event_type == "turn_end":
            self._set_busy(False)
            if self._streaming_text:
                self._chat_log.finalize_assistant(self._streaming_text, self._stream_id)
                self._streaming_text = ""
            self._update_footer()
            self._update_header()

        elif event_type == "command_result":
            output = event.get("output", "")
            if output:
                self._chat_log.add_system(output)
            if event.get("exit"):
                self._request_exit()
            self._update_footer()

        elif event_type == "error":
            message = event.get("message", "Unknown error")
            self._set_busy(False)
            self._chat_log.drop_assistant(self._stream_id)
            self._chat_log.add_system(self._theme.error(f"Error: {message}"))
            self._streaming_text = ""
            self._update_footer()


def run_interactive_mode(
    session: AgentSession,
    *,
    verbose: bool = False,
) -> None:
    """Run the interactive TUI mode (blocking).

    This is the main entry point for the full interactive experience.
    """
    mode = InteractiveMode(session, verbose=verbose)
    mode.run()


_DIFF_LINE_RE = re.compile(r"^[+\-]\s*\d+\s", re.MULTILINE)


def _is_diff_result(text: str) -> bool:
    """Check if tool result contains a custom diff (``+linenum`` / ``-linenum`` format)."""
    return _DIFF_LINE_RE.search(text) is not None
