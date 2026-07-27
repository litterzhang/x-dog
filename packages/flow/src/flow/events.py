"""flow.events — typed lifecycle events for workflow execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class NodeStarted:
    node_id: str
    step: int


@dataclass(frozen=True)
class NodeFinished:
    node_id: str
    step: int
    duration_s: float  # wall-clock seconds for this node
    tokens: int = 0  # total tokens (agent nodes); 0 for script nodes


@dataclass(frozen=True)
class NodeFailed:
    node_id: str
    step: int
    duration_s: float
    error: str  # f"{type(exc).__name__}: {exc}"


FlowEvent = NodeStarted | NodeFinished | NodeFailed
EventCallback = Callable[[FlowEvent], None]
