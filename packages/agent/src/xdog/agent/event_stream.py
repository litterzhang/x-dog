"""Agent-scoped async event stream for ``AgentEvent`` items.

The ``pi_ai.utils.event_stream.EventStream`` class is strongly typed to
:class:`~pi_ai.types.AssistantMessageEvent` and includes a *result* future
pattern designed for LLM streaming.  The agent layer needs a simpler,
purely push-based stream typed to :data:`~agent.types.AgentEvent`.

This module provides :class:`AgentEventStream`, a lightweight async
iterable with push/close semantics that mirrors the ``EventStream`` API
but is generic over the event type.  It also supports a *result* future
so that callers can await a final value after the stream ends (matching
the TS ``EventStream<AgentEvent, AgentMessage[]>`` pattern).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Callable, Generic, TypeVar

E = TypeVar("E")


class AgentEventStream(Generic[E]):
    """Push-based async iterable of agent lifecycle events.

    Usage -- **producer** side::

        stream = AgentEventStream[AgentEvent]()
        await stream.send(AgentStartEvent())
        ...
        stream.end(result_value)

    Usage -- **consumer** side::

        async for event in stream:
            print(event.type)

        result = await stream.result()
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[E | None] = asyncio.Queue()
        self._closed = False
        self._listeners: list[Callable[[E], Any]] = []
        # Result support (matches ai.utils.event_stream.EventStream)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        self._result_future: asyncio.Future[Any] = loop.create_future()

    # -- producer API -------------------------------------------------------

    async def send(self, event: E) -> None:
        """Push an event into the stream."""
        if self._closed:
            return
        await self._queue.put(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # listeners must not break the stream

    def send_sync(self, event: E) -> None:
        """Push an event without awaiting (fire-and-forget into the queue)."""
        if self._closed:
            return
        self._queue.put_nowait(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass

    def end(self, result: Any = None) -> None:
        """Signal end of stream with a result value.

        This is the primary way to close the stream.  The *result* is
        available to consumers via :meth:`result`.
        """
        if self._closed:
            return
        self._closed = True
        if not self._result_future.done():
            self._result_future.set_result(result)
        self._queue.put_nowait(None)  # sentinel

    async def close(self) -> None:
        """Signal the end of the stream without a result value."""
        if self._closed:
            return
        self._closed = True
        if not self._result_future.done():
            self._result_future.set_result(None)
        await self._queue.put(None)  # sentinel

    def close_sync(self) -> None:
        """Signal end of stream synchronously."""
        if self._closed:
            return
        self._closed = True
        if not self._result_future.done():
            self._result_future.set_result(None)
        self._queue.put_nowait(None)

    # -- consumer API -------------------------------------------------------

    def on(self, listener: Callable[[E], Any]) -> Callable[[], None]:
        """Register a synchronous listener.  Returns an unsubscribe callable."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    async def collect(self) -> list[E]:
        """Consume the entire stream into a list."""
        items: list[E] = []
        async for item in self:
            items.append(item)
        return items

    async def result(self) -> Any:
        """Await the final result after the stream ends.

        If the stream has not been consumed yet, this will first drain
        all remaining events.
        """
        if not self._result_future.done():
            async for _ in self:
                pass
        return await self._result_future

    # -- async iterator protocol --------------------------------------------

    def __aiter__(self) -> AsyncIterator[E]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[E]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` or :meth:`end` has been called."""
        return self._closed
