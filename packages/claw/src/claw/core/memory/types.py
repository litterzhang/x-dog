"""Memory types — shared across memory subsystem."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryChunk:
    """A scored chunk of text from memory search."""

    text: str = ""
    source: str = ""
    score: float = 0.0
    timestamp: float = 0.0
