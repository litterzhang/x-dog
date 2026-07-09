"""Lightweight event bus for intra-session communication."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


EventHandler = Callable[..., Awaitable[None]]


@dataclass
class EventBus:
    """Simple publish/subscribe event bus.

    Events are keyed by string names.  Handlers are async callables.
    """

    _handlers: dict[str, list[EventHandler]] = field(
        default_factory=lambda: defaultdict(list),
    )

    def on(self, event: str, handler: EventHandler) -> None:
        """Subscribe *handler* to *event*."""
        self._handlers[event].append(handler)

    def off(self, event: str, handler: EventHandler) -> None:
        """Unsubscribe *handler* from *event*."""
        try:
            self._handlers[event].remove(handler)
        except ValueError:
            pass

    async def emit(self, event: str, **kwargs: Any) -> None:
        """Emit *event*, calling all subscribed handlers concurrently."""
        handlers = list(self._handlers.get(event, []))
        if not handlers:
            return
        await asyncio.gather(*(h(**kwargs) for h in handlers))

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._handlers.clear()


# Module-level singleton so the whole session can share one bus.
_global_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return (and lazily create) the global event bus."""
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus
