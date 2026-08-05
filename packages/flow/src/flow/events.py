"""flow.events — typed lifecycle events for workflow execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class NodeStarted:
    node_id: str
    step: int
    # A lossy one-line rendering of the inputs this activation was handed, from
    # flow.preview.preview_ports. "" when the node takes none. For reading, not
    # parsing — the authoritative values are the run result's stack frames.
    inputs_preview: str = ""


@dataclass(frozen=True)
class NodeFinished:
    node_id: str
    step: int
    duration_s: float  # wall-clock seconds for this node
    tokens: int = 0  # total tokens (agent nodes); 0 for script nodes
    # How many times the node's work ran in THIS activation: 1 for an ordinary
    # node, len(items) for a fan-out node (whose instances share one activation,
    # one trace frame, and one completed id).  Summing this across activations is
    # the only exact way to count a node's invocations — the trace cannot express
    # it, and stub-based counting misses nodes that run for real.
    instances: int = 1
    # The same lossy rendering, over the output ports the node just stored.
    output_preview: str = ""


@dataclass(frozen=True)
class NodeFailed:
    node_id: str
    step: int
    duration_s: float
    error: str  # f"{type(exc).__name__}: {exc}"


FlowEvent = NodeStarted | NodeFinished | NodeFailed
EventCallback = Callable[[FlowEvent], None]
