"""flow.errors — workflow exception hierarchy."""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for all flow workflow errors."""


class WorkflowValidationError(WorkflowError):
    """Raised when a workflow definition fails validation."""


class WorkflowExecutionError(WorkflowError):
    """Raised when a workflow fails during execution."""


class WorkflowPaused(Exception):
    """Raised when a human node pauses the run awaiting an external signal."""

    def __init__(self, node_id: str, signal: str) -> None:
        super().__init__(f"paused at {node_id!r} awaiting signal {signal!r}")
        self.node_id = node_id
        self.signal = signal
