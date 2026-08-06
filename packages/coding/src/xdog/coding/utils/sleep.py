"""Sleep utility: async and sync sleep with cancellation support."""

from __future__ import annotations

import asyncio
import time


def sync_sleep(seconds: float) -> None:
    """Block the current thread for *seconds*."""
    time.sleep(seconds)


async def async_sleep(seconds: float) -> None:
    """Async sleep for *seconds*."""
    await asyncio.sleep(seconds)


class InterruptibleSleep:
    """An async sleep that can be cancelled externally.

    Usage::

        sleeper = InterruptibleSleep()
        # In one coroutine:
        await sleeper.sleep(30)
        # In another:
        sleeper.wake()
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    async def sleep(self, seconds: float) -> bool:
        """Sleep for *seconds* or until woken.

        Returns ``True`` if the full duration elapsed, ``False`` if
        interrupted early.
        """
        self._event.clear()
        try:
            await asyncio.wait_for(self._event.wait(), timeout=seconds)
            return False  # Was woken early
        except asyncio.TimeoutError:
            return True  # Full duration elapsed

    def wake(self) -> None:
        """Interrupt a pending sleep."""
        self._event.set()
