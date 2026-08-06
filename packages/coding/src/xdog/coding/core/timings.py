"""Timing utilities for performance instrumentation."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

from xdog.coding.core.defaults import TIMING_ENABLED


@dataclass
class TimingEntry:
    """A single timing measurement."""

    label: str
    start: float
    end: float = 0.0

    @property
    def elapsed_ms(self) -> float:
        return (self.end - self.start) * 1000.0


class TimingCollector:
    """Collects and reports timing information."""

    def __init__(self, *, enabled: bool = TIMING_ENABLED) -> None:
        self._enabled = enabled
        self._entries: list[TimingEntry] = []

    @contextmanager
    def measure(self, label: str) -> Generator[None, None, None]:
        """Context manager that records the elapsed time for *label*."""
        if not self._enabled:
            yield
            return
        entry = TimingEntry(label=label, start=time.perf_counter())
        try:
            yield
        finally:
            entry.end = time.perf_counter()
            self._entries.append(entry)

    def record(self, label: str, elapsed_ms: float) -> None:
        """Manually record a timing entry."""
        if not self._enabled:
            return
        now = time.perf_counter()
        self._entries.append(TimingEntry(
            label=label,
            start=now - (elapsed_ms / 1000.0),
            end=now,
        ))

    def get_entries(self) -> list[TimingEntry]:
        return list(self._entries)

    def summary(self) -> str:
        """Return a formatted summary of all collected timings."""
        if not self._entries:
            return "No timings recorded."
        lines = ["Timing Summary:", ""]
        for entry in self._entries:
            lines.append(f"  {entry.label:<40s}  {entry.elapsed_ms:>8.1f} ms")
        total = sum(e.elapsed_ms for e in self._entries)
        lines.append("")
        lines.append(f"  {'Total':<40s}  {total:>8.1f} ms")
        return "\n".join(lines)

    def clear(self) -> None:
        self._entries.clear()


# Module-level singleton
_collector: TimingCollector | None = None


def get_timing_collector() -> TimingCollector:
    global _collector
    if _collector is None:
        _collector = TimingCollector()
    return _collector
