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

from xdog.agent import (
    AgentEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
)
from xdog.ai.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionDecision, PermissionRequest
from xdog.coding.core.slash_commands import execute_command, parse_slash_command
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
from xdog.coding.modes.interactive.components.footer import FooterComponent
from xdog.coding.modes.interactive.components.permission_prompt import PermissionPromptComponent
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.components.loader import Loader
from xdog.tui.components.text import Text
from xdog.tui.tui import TUI, Container

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


def _context_tokens_from_messages(messages: list[Any]) -> int:
    """Tokens occupying the context window, per the latest assistant turn.

    Cached prefix tokens still occupy the window, so ``input`` alone would
    under-report; cache buckets are included.
    """
    for msg in reversed(messages):
        usage = getattr(msg, "usage", None)
        if usage is None:
            continue
        total = int(usage.input) + int(usage.cache_read) + int(usage.cache_write)
        if total > 0:
            return total
    return 0


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
        self._last_busy_label = ""
        self._streaming_text = ""
        self._streaming_thinking = ""
        self._stream_sequence = 0
        self._stream_id = "assistant-0"
        self._last_tool_component: Any = None
        self._permission_prompt: PermissionPromptComponent | None = None
        self._awaiting_permission = False

        # Event queue for thread-safe UI updates from agent events
        self._event_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        # Build component tree
        self._tui = TUI()

        self._header = Text("", 1, 0)
        self._chat_log = ChatLog(self._theme)
        self._status_container = Container()
        self._footer = FooterComponent(self._theme)
        self._editor = CustomEditorComponent(self._theme)
        self._permission_container = Container()

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
        # Approval belongs directly after the input, not over conversation
        # history. The container is empty except while a call is pending.
        root.add_child(self._permission_container)

        self._tui.add_child(root)
        self._tui.set_focus(self._editor)

        # Wire editor callbacks
        self._editor.on_submit = self._handle_submit
        self._editor.on_ctrl_c = self._handle_ctrl_c
        self._editor.on_ctrl_d = self._request_exit
        self._editor.on_escape = self._handle_escape

        # Subscribe to agent events and permission requests. The latter callback
        # runs in the background agent thread and only enqueues TUI work.
        self._unsubscribe = self._session.agent.subscribe(self._on_agent_event)
        self._session.permissions.set_request_handler(self._on_permission_request)

    def run(self) -> None:
        """Start the interactive TUI (blocking)."""
        self._update_header()
        self._update_footer()

        model_name = self._session.model or "unknown"
        thinking = self._session.agent.options.thinking or "off"
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
            self._session.permissions.set_request_handler(None)
            self._session.permissions.deny_all()
            if self._unsubscribe is not None:
                self._unsubscribe()

    def _replay_history(self) -> None:
        """Replay messages, including persisted tool calls and their results."""
        tool_components: dict[str, Any] = {}
        for msg in self._session.messages:
            if isinstance(msg, UserMessage):
                text = msg.content if isinstance(msg.content, str) else ""
                if text:
                    self._chat_log.add_user(text)
                    self._editor.add_to_history(text)
            elif isinstance(msg, AssistantMessage):
                text, thinking = _assistant_content(msg)
                if text or thinking:
                    self._chat_log.add_assistant(text, thinking=thinking)
                for part in msg.content:
                    if isinstance(part, ToolCall):
                        tool_components[part.id] = self._chat_log.add_tool(
                            part.name,
                            part.arguments,
                        )
            elif isinstance(msg, ToolResultMessage):
                component = tool_components.pop(msg.tool_call_id, None)
                if component is None:
                    component = self._chat_log.add_tool(msg.tool_name, None)
                result_text = "\n".join(
                    part.text for part in msg.content if isinstance(part, TextContent)
                )
                component.set_result(result_text, is_error=msg.is_error)

    # -- Header / Footer --

    def _update_header(self) -> None:
        model_name = self._session.model or "unknown"
        self._header.set_text(
            self._theme.header(f"  coding | {model_name}")
        )

    def _update_footer(self) -> None:
        model_name = self._session.model or "unknown"
        thinking = self._session.agent.options.thinking or "off"
        self._footer.update(
            model=model_name,
            session_id=self._session.session_id,
            message_count=len(self._session.messages),
            thinking=str(thinking),
            permission_mode=self._session.permissions.mode,
            working_dir=str(self._session.working_dir),
            context_tokens=_context_tokens_from_messages(self._session.messages),
            max_context=self._session.context_limit,
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
        self._last_busy_label = message
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
            self._last_busy_label = ""
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
        self._start_turn(value)

    def _start_turn(self, message: str, *, echo: bool = True) -> None:
        """Run one agent turn in a background thread.

        ``echo`` is False for text the user did not type — a skill's
        instructions can run to hundreds of lines, and replaying them into the
        chat log would bury the conversation they were meant to help with.
        """
        if echo:
            self._chat_log.add_user(message)
        self._set_busy(True)
        self._streaming_text = ""
        self._streaming_thinking = ""

        thread = threading.Thread(
            target=self._run_agent_turn,
            args=(message,),
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
                "prompt": result.prompt,
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
        if isinstance(event, MessageStartEvent):
            if isinstance(event.message, AssistantMessage):
                self._event_queue.put({"type": "assistant_start"})

        elif isinstance(event, (MessageUpdateEvent, MessageEndEvent)):
            # Some providers emit no incremental text event and deliver the
            # complete response only in MessageEndEvent. Handling both keeps
            # ordinary assistant text visible for streaming and non-streaming
            # response paths.
            msg = event.message
            if isinstance(msg, AssistantMessage):
                text, thinking = _assistant_content(msg)
                if text or thinking:
                    self._event_queue.put({
                        "type": "text_update",
                        "text": text,
                        "thinking": thinking,
                    })
                if isinstance(event, MessageEndEvent):
                    self._event_queue.put({"type": "assistant_end"})

        elif isinstance(event, TurnEndEvent):
            # One user prompt can contain several model/tool turns. Refresh the
            # footer after each one so message count and context usage do not
            # remain stale until the entire agent loop finishes.
            self._event_queue.put({"type": "turn_footer_update"})

        elif isinstance(event, ToolExecutionStartEvent):
            self._event_queue.put({
                "type": "tool_call",
                "name": event.tool_name,
                "arguments": event.args,
            })

        elif isinstance(event, ToolExecutionUpdateEvent):
            if event.partial_result is not None:
                update_parts: list[str] = []
                for update_part in event.partial_result.content:
                    if isinstance(update_part, TextContent):
                        update_parts.append(update_part.text)
                if update_parts:
                    self._event_queue.put({
                        "type": "tool_update",
                        "name": event.tool_name,
                        "text": "".join(update_parts),
                    })

        elif isinstance(event, ToolExecutionEndEvent):
            result_text = ""
            if event.result is not None:
                # Extract text content from the result
                for result_part in event.result.content:
                    if isinstance(result_part, TextContent):
                        result_text = result_part.text
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

    def _on_permission_request(self, request: PermissionRequest) -> None:
        self._event_queue.put({"type": "permission_request", "request": request})

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

        if self._is_busy and not self._awaiting_permission:
            elapsed = self._format_elapsed()
            label = f"thinking... • {elapsed}"
            # The spinner has its own 80 ms render timer. Only change its text
            # when the elapsed label changes instead of invalidating the whole
            # TUI on every 30 FPS poll.
            if self._status_loader and label != self._last_busy_label:
                self._status_loader.set_message(label)
                self._last_busy_label = label
                changed = True

        if changed:
            self._tui.request_render()

    def _show_permission_request(self, request: PermissionRequest) -> None:
        """Display a focused approval panel immediately after the input."""
        self._permission_container.clear()

        def _decide(decision: PermissionDecision) -> None:
            self._session.permissions.resolve(request.id, decision)
            self._permission_container.clear()
            self._permission_prompt = None
            self._awaiting_permission = False
            self._tui.set_focus(self._editor)
            if self._is_busy:
                self._set_status_busy("resuming...")
            self._tui.request_render()

        prompt = PermissionPromptComponent(request, self._theme, _decide)
        self._permission_prompt = prompt
        self._permission_container.add_child(prompt)
        self._tui.set_focus(prompt)
        self._awaiting_permission = True
        self._set_status_text("awaiting tool permission")

    def _finalize_streaming_assistant(self) -> None:
        """Finish the current model message without crossing tool boundaries."""
        if self._streaming_text or self._streaming_thinking:
            self._chat_log.finalize_assistant(
                self._streaming_text,
                self._stream_id,
                thinking=self._streaming_thinking,
            )
        self._streaming_text = ""
        self._streaming_thinking = ""

    def _handle_ui_event(self, event: dict[str, Any]) -> None:
        """Handle a UI event from the event queue."""
        event_type = event.get("type")

        if event_type == "assistant_start":
            # Every model response gets its own chat component. Reusing one
            # component for the whole agent loop caused a post-tool answer to
            # replace the pre-tool reasoning in place, making the latest text
            # appear before the tool result.
            self._finalize_streaming_assistant()
            self._stream_sequence += 1
            self._stream_id = f"assistant-{self._stream_sequence}"

        elif event_type == "text_update":
            text = event.get("text", "")
            thinking = event.get("thinking", "")
            self._streaming_text = text
            self._streaming_thinking = thinking
            self._chat_log.update_assistant(
                text,
                self._stream_id,
                thinking=thinking,
            )

        elif event_type == "tool_call":
            name = event.get("name", "")
            arguments = event.get("arguments", {})
            self._chat_log.add_tool(name, arguments)
            self._last_tool_component = self._chat_log.get_last_tool()

        elif event_type == "permission_request":
            request = event.get("request")
            if isinstance(request, PermissionRequest):
                self._show_permission_request(request)

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

        elif event_type == "assistant_end":
            self._finalize_streaming_assistant()

        elif event_type == "turn_footer_update":
            self._update_footer()
            self._update_header()

        elif event_type == "turn_end":
            self._set_busy(False)
            self._finalize_streaming_assistant()
            self._update_footer()
            self._update_header()

        elif event_type == "command_result":
            output = event.get("output", "")
            if output:
                self._chat_log.add_system(output)
            if event.get("exit"):
                self._request_exit()
            prompt = event.get("prompt", "")
            if prompt:
                # A skill command: carry its instructions into a real turn.
                self._start_turn(prompt, echo=False)
            self._update_footer()

        elif event_type == "error":
            message = event.get("message", "Unknown error")
            self._set_busy(False)
            self._chat_log.drop_assistant(self._stream_id)
            self._chat_log.add_system(self._theme.error(f"Error: {message}"))
            self._streaming_text = ""
            self._streaming_thinking = ""
            self._update_footer()


def _assistant_content(message: AssistantMessage) -> tuple[str, str]:
    """Extract visible response and reasoning text from an assistant message."""
    text: list[str] = []
    thinking: list[str] = []
    for part in message.content:
        if isinstance(part, TextContent):
            text.append(part.text)
        elif isinstance(part, ThinkingContent) and not part.redacted:
            thinking.append(part.thinking)
    return "".join(text), "".join(thinking)


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
