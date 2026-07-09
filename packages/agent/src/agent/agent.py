"""High-level Agent class with state management, event subscriptions, and message queuing.

Usage::

    from agent import Agent
    from agent.helpers import stream_fn_from_provider
    import ai

    agent = Agent(
        stream_fn_from_provider(ai.provider("copilot")),
        config=AgentConfig(model=model, system_prompt="You are helpful."),
        tools=[search_tool, code_tool],
    )

    event_stream = await agent.prompt("Hello!")
    async for event in event_stream:
        print(event)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any, Callable

from agent.agent_loop import run_agent_loop, run_agent_loop_continue
from agent.event_stream import AgentEventStream
from agent.core import (
    AgentConfig,
    AgentContext,
    AgentMessage,
    AgentState,
    AgentTool,
    EmbedFn,
    QueueMode,
    StreamFn,
    ToolExecutionMode,
    WebSearchFn,
)
from agent.events import (
    AgentEndEvent,
    AgentEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)
from agent.types import (
    AfterToolCallFn,
    AgentLoopConfig,
    BeforeToolCallFn,
    ConvertToLlmFn,
    TransformContextFn,
)
from ai.types import (
    AssistantMessage,
    ImageContent,
    Message,
    StreamOptions,
    TextContent,
    UserMessage,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

EventListener = Callable[[AgentEvent], None]
"""Synchronous callback invoked for every agent lifecycle event."""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    """High-level agent with state management, event subscriptions, and queuing.

    The Agent wraps the low-level :func:`agent_loop` with:

    * Persistent conversation state (:class:`AgentState`)
    * Subscriber-based event dispatching
    * Steering interrupts (:meth:`steer`) and follow-up injection
      (:meth:`follow_up`)
    * Cancellation via :meth:`abort`

    Parameters
    ----------
    stream_fn:
        Required. The function that calls the LLM. Build one from an ai
        Provider via ``stream_fn_from_provider(provider)``.
    config:
        Agent configuration (model, system_prompt, limits, capabilities,
        StreamOptions for LLM calls).
    tools:
        Tools available to the agent.
    embed_fn:
        If provided, a built-in ``embed`` tool is auto-registered.
    web_search_fn:
        If provided, a built-in ``web_search`` tool is auto-registered.
    convert_to_llm:
        Optional message converter.  Defaults to identity (plain Messages).
    transform_context:
        Optional async context transform (summarisation, RAG, etc.).
    before_tool_call:
        Async hook called before each tool execution; can block calls.
    after_tool_call:
        Async hook called after each tool execution; can override results.
    """

    def __init__(
        self,
        stream_fn: StreamFn,
        *,
        config: AgentConfig | None = None,
        tools: tuple[AgentTool, ...] | list[AgentTool] | None = None,
        tool_ctx: dict[str, Any] | None = None,
        embed_fn: EmbedFn | None = None,
        web_search_fn: WebSearchFn | None = None,
        convert_to_llm: ConvertToLlmFn | None = None,
        transform_context: TransformContextFn | None = None,
        before_tool_call: BeforeToolCallFn | None = None,
        after_tool_call: AfterToolCallFn | None = None,
    ) -> None:
        cfg = config or AgentConfig()

        # Build tools list — start with user-provided tools
        tools_list = list(tools) if tools else []

        # Auto-create tools from injected functions
        if web_search_fn is not None:
            from agent.tools import create_web_search_tool_from_fn
            tools_list.append(create_web_search_tool_from_fn(web_search_fn))

        if embed_fn is not None:
            from agent.tools import create_embed_tool_from_fn
            tools_list.append(create_embed_tool_from_fn(embed_fn))

        self._state = AgentState(
            system_prompt=cfg.system_prompt,
            model=cfg.model,
            tools=tuple(tools_list),
            messages=(),
        )

        self._stream_fn = stream_fn
        self._config = cfg
        self._options = cfg.options
        self._tool_ctx = tool_ctx or {}
        self._convert_to_llm = convert_to_llm
        self._transform_context = transform_context
        self._tool_execution: ToolExecutionMode = cfg.tool_execution
        self._before_tool_call = before_tool_call
        self._after_tool_call = after_tool_call
        self._steering_mode = cfg.steering_mode
        self._follow_up_mode = cfg.follow_up_mode

        # Cancellation
        self._cancel = asyncio.Event()

        # Message queues
        self._steering_queue: list[AgentMessage] = []
        self._follow_up_queue: list[AgentMessage] = []

        # Event listeners
        self._listeners: list[EventListener] = []

        # Current event stream reference
        self._current_stream: AgentEventStream[AgentEvent] | None = None

        # Idle tracking (for wait_for_idle)
        self._idle_event = asyncio.Event()
        self._idle_event.set()  # starts idle

    # -- Public properties ---------------------------------------------------

    @property
    def state(self) -> AgentState:
        """Current agent state (read-only snapshot)."""
        return self._state

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        """Shortcut to the conversation message history."""
        return self._state.messages

    @property
    def is_streaming(self) -> bool:
        """Whether the agent is currently in a streaming loop."""
        return self._state.is_streaming

    @property
    def stream_fn(self) -> StreamFn:
        """The stream function used by this agent."""
        return self._stream_fn

    @property
    def tool_execution(self) -> ToolExecutionMode:
        """Get the current tool execution mode."""
        return self._tool_execution

    @property
    def options(self) -> StreamOptions:
        """The StreamOptions used for LLM calls."""
        return self._options

    def set_options(self, options: StreamOptions) -> None:
        """Replace the StreamOptions (temperature, thinking, max_tokens, etc.)."""
        self._options = options

    # -- State updates (immutable-style) ------------------------------------

    def _update_state(self, **kwargs: Any) -> None:
        """Replace the state with a new instance carrying the given overrides."""
        self._state = replace(self._state, **kwargs)

    # -- Public state mutators -----------------------------------------------

    def set_system_prompt(self, prompt: str | tuple | None) -> None:
        """Update the system prompt. Accepts string or SystemPromptBlock tuple."""
        self._update_state(system_prompt=prompt)

    def set_model(self, model: str) -> None:
        """Update the model name."""
        self._update_state(model=model)

    def set_tools(self, tools: list[AgentTool] | tuple[AgentTool, ...]) -> None:
        """Replace the tool set."""
        self._update_state(tools=tuple(tools))

    def set_tool_execution(self, value: ToolExecutionMode) -> None:
        """Set tool execution mode."""
        self._tool_execution = value

    def set_before_tool_call(self, value: BeforeToolCallFn | None) -> None:
        """Set the before_tool_call hook."""
        self._before_tool_call = value

    def set_after_tool_call(self, value: AfterToolCallFn | None) -> None:
        """Set the after_tool_call hook."""
        self._after_tool_call = value

    def set_steering_mode(self, mode: QueueMode) -> None:
        """Set the steering queue consumption mode."""
        self._steering_mode = mode

    def set_follow_up_mode(self, mode: QueueMode) -> None:
        """Set the follow-up queue consumption mode."""
        self._follow_up_mode = mode

    def replace_messages(self, messages: list[AgentMessage]) -> None:
        """Replace the entire message history."""
        self._update_state(messages=tuple(messages))

    def append_message(self, message: AgentMessage) -> None:
        """Append a single message to the history."""
        self._update_state(messages=(*self._state.messages, message))

    def clear_messages(self) -> None:
        """Clear the message history."""
        self._update_state(messages=())

    def reset(self) -> None:
        """Reset the agent to a clean state (messages, error, streaming)."""
        self._update_state(
            messages=(),
            error=None,
            is_streaming=False,
            stream_message=None,
            pending_tool_calls=frozenset(),
        )
        self._steering_queue.clear()
        self._follow_up_queue.clear()

    # -- Queue management ----------------------------------------------------

    def clear_steering_queue(self) -> None:
        """Discard all pending steering messages."""
        self._steering_queue.clear()

    def clear_follow_up_queue(self) -> None:
        """Discard all pending follow-up messages."""
        self._follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        """Discard all pending steering and follow-up messages."""
        self._steering_queue.clear()
        self._follow_up_queue.clear()

    def has_queued_messages(self) -> bool:
        """Return ``True`` if any steering or follow-up messages are queued."""
        return bool(self._steering_queue or self._follow_up_queue)

    # -- Event system --------------------------------------------------------

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        """Register an event listener.  Returns an unsubscribe function."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _emit(self, event: AgentEvent) -> None:
        """Dispatch an event to all registered listeners."""
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("Error in agent event listener")

    # -- Prompt / Continue ---------------------------------------------------

    async def prompt(
        self,
        message: str | AgentMessage | list[AgentMessage],
        images: list[ImageContent] | None = None,
    ) -> AgentEventStream[AgentEvent]:
        """Start a new agent turn with a user message.

        Supports multiple overloads matching the TS Agent:
        - ``prompt("text")`` — creates a UserMessage with text content.
        - ``prompt("text", images=[...])`` — creates a UserMessage with text + images.
        - ``prompt(agent_message)`` — uses the message directly.
        - ``prompt([msg1, msg2, ...])`` — multiple messages at once.

        Returns
        -------
        AgentEventStream[AgentEvent]
            Async iterable of agent lifecycle events.
        """
        if self._state.is_streaming:
            raise RuntimeError(
                "Agent is already processing a prompt. "
                "Use steer() or follow_up() to queue messages, or wait for completion."
            )

        if not self._state.model:
            raise RuntimeError("No model configured")

        msgs: list[AgentMessage]

        if isinstance(message, list):
            msgs = message
        elif isinstance(message, str):
            content_parts: list[TextContent | ImageContent] = [TextContent(text=message)]
            if images:
                content_parts.extend(images)
            msgs = [UserMessage(content=tuple(content_parts))]
        else:
            msgs = [message]

        return await self._run_loop(msgs)

    async def continue_(self) -> AgentEventStream[AgentEvent]:
        """Continue from current context (used for retries and resuming queued messages).

        Matches the TS ``continue()`` method:
        - If last message is assistant, drains steering queue first, then follow-up.
        - Otherwise, continues from the existing context.
        """
        if self._state.is_streaming:
            raise RuntimeError(
                "Agent is already processing. Wait for completion before continuing."
            )

        current_messages = list(self._state.messages)
        if not current_messages:
            raise RuntimeError("No messages to continue from")

        last = current_messages[-1]
        if isinstance(last, AssistantMessage):
            # Drain steering queue first
            steering = self._dequeue_steering()
            if steering:
                return await self._run_loop(steering, skip_initial_steering_poll=True)

            # Then try follow-up queue
            follow_ups = self._dequeue_follow_ups()
            if follow_ups:
                return await self._run_loop(follow_ups)

            raise RuntimeError("Cannot continue from message role: assistant")

        return await self._run_loop(None)

    # -- Steering & follow-up ------------------------------------------------

    def steer(self, message: str | AgentMessage) -> None:
        """Queue a steering interrupt.

        The next tool-execution checkpoint will pick up this message and
        skip remaining tool calls, letting the model respond to the
        steering input instead.
        """
        if isinstance(message, str):
            msg: AgentMessage = UserMessage(content=message)
        else:
            msg = message
        self._steering_queue.append(msg)

    def follow_up(self, message: str | AgentMessage) -> None:
        """Queue a follow-up message.

        Follow-ups are appended after all tool calls in a turn complete,
        causing the model to continue with the additional context.
        """
        if isinstance(message, str):
            msg: AgentMessage = UserMessage(content=message)
        else:
            msg = message
        self._follow_up_queue.append(msg)

    # -- Abort ---------------------------------------------------------------

    def abort(self) -> None:
        """Cancel the current agent loop."""
        self._cancel.set()
        self._update_state(is_streaming=False)

    def reset_abort(self) -> None:
        """Reset the cancellation flag so a new loop can start."""
        self._cancel = asyncio.Event()

    # -- Wait for idle -------------------------------------------------------

    async def wait_for_idle(self) -> None:
        """Wait until the current agent loop finishes.

        Returns immediately if the agent is not streaming.
        """
        await self._idle_event.wait()

    # -- Internal: queue draining --------------------------------------------

    def _dequeue_steering(self) -> list[AgentMessage]:
        """Drain the steering queue based on the configured mode."""
        if not self._steering_queue:
            return []

        if self._steering_mode == QueueMode.ALL:
            msgs = list(self._steering_queue)
            self._steering_queue.clear()
            return msgs

        # ONE_AT_A_TIME
        return [self._steering_queue.pop(0)]

    def _dequeue_follow_ups(self) -> list[AgentMessage]:
        """Drain the follow-up queue based on the configured mode."""
        if not self._follow_up_queue:
            return []

        if self._follow_up_mode == QueueMode.ALL:
            msgs = list(self._follow_up_queue)
            self._follow_up_queue.clear()
            return msgs

        return [self._follow_up_queue.pop(0)]

    # -- Internal: queue callbacks for agent_loop ----------------------------

    async def _get_steering_messages(self) -> list[AgentMessage]:
        """Drain the steering queue (async version for agent_loop config)."""
        return self._dequeue_steering()

    async def _get_follow_up_messages(self) -> list[AgentMessage]:
        """Drain the follow-up queue (async version for agent_loop config)."""
        return self._dequeue_follow_ups()

    # -- Internal run --------------------------------------------------------

    async def _run_loop(
        self,
        messages: list[AgentMessage] | None,
        *,
        skip_initial_steering_poll: bool = False,
    ) -> AgentEventStream[AgentEvent]:
        """Build the loop config and start the agent loop."""
        self._cancel = asyncio.Event()
        self._idle_event.clear()

        self._update_state(
            is_streaming=True,
            stream_message=None,
            pending_tool_calls=frozenset(),
            error=None,
        )

        context = AgentContext(
            system_prompt=self._state.system_prompt,
            messages=list(self._state.messages),
            tools=list(self._state.tools) if self._state.tools else None,
        )

        # Inject cancel event into StreamOptions for this run
        from dataclasses import replace as _replace
        options = _replace(self._options, cancel=self._cancel)

        # Wrap steering callback to support skipInitialSteeringPoll
        skip_steering = skip_initial_steering_poll

        async def _steering_cb() -> list[AgentMessage]:
            nonlocal skip_steering
            if skip_steering:
                skip_steering = False
                return []
            return self._dequeue_steering()

        config = AgentLoopConfig(
            tool_execution=self._tool_execution,
            before_tool_call=self._before_tool_call,
            after_tool_call=self._after_tool_call,
            convert_to_llm=self._convert_to_llm or _default_convert_to_llm,
            transform_context=self._transform_context,
            get_steering_messages=_steering_cb,
            get_follow_up_messages=self._get_follow_up_messages,
        )

        stream: AgentEventStream[AgentEvent] = AgentEventStream()
        self._current_stream = stream

        async def _run_and_wrap() -> None:
            try:
                if messages is not None:
                    await run_agent_loop(
                        messages,
                        context,
                        config,
                        lambda event: self._process_loop_event(event, stream),
                        self._stream_fn,
                        self._state.model,
                        options,
                        self._cancel,
                        self._tool_ctx,
                    )
                else:
                    await run_agent_loop_continue(
                        context,
                        config,
                        lambda event: self._process_loop_event(event, stream),
                        self._stream_fn,
                        self._state.model,
                        options,
                        self._cancel,
                        self._tool_ctx,
                    )
            except Exception as exc:
                logger.exception("Error in agent loop")
                self._update_state(
                    is_streaming=False,
                    error=str(exc),
                )
                error_msg = AssistantMessage(
                    content=(TextContent(text=f"Agent error: {exc}"),),
                    stop_reason="error",
                    error_message=str(exc),
                )
                self.append_message(error_msg)
                error_end = AgentEndEvent(
                    messages=(*self._state.messages,),
                )
                self._emit(error_end)
                await stream.send(error_end)
            finally:
                self._update_state(
                    is_streaming=False,
                    stream_message=None,
                    pending_tool_calls=frozenset(),
                )
                self._idle_event.set()
                await stream.close()

        asyncio.ensure_future(_run_and_wrap())
        return stream

    def _process_loop_event(
        self,
        event: AgentEvent,
        stream: AgentEventStream[AgentEvent],
    ) -> None:
        """Update internal state in response to lifecycle events."""
        if isinstance(event, MessageStartEvent):
            self._update_state(stream_message=event.message)

        elif isinstance(event, MessageUpdateEvent):
            self._update_state(stream_message=event.message)

        elif isinstance(event, MessageEndEvent):
            self._update_state(stream_message=None)
            if event.message is not None:
                self.append_message(event.message)

        elif isinstance(event, ToolExecutionStartEvent):
            pending = self._state.pending_tool_calls | frozenset({event.tool_call_id})
            self._update_state(pending_tool_calls=pending)

        elif isinstance(event, ToolExecutionEndEvent):
            pending = self._state.pending_tool_calls - frozenset({event.tool_call_id})
            self._update_state(pending_tool_calls=pending)

        elif isinstance(event, TurnEndEvent):
            if (event.message is not None
                    and isinstance(event.message, AssistantMessage)
                    and event.message.error_message):
                self._update_state(error=event.message.error_message)

        elif isinstance(event, AgentEndEvent):
            self._update_state(
                is_streaming=False,
                stream_message=None,
            )

        self._emit(event)
        stream.send_sync(event)


# ---------------------------------------------------------------------------
# Default converter
# ---------------------------------------------------------------------------

def _default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Identity converter -- keeps only instances of the ``Message`` union."""
    from ai.types import AssistantMessage, ToolResultMessage, UserMessage

    return [
        m for m in messages
        if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
    ]
