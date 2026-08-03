"""flow.errors — workflow exception hierarchy."""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for all flow workflow errors."""


class WorkflowValidationError(WorkflowError):
    """Raised when a workflow definition fails validation."""


class FlowWarning(UserWarning):
    """A non-fatal validation concern — the workflow is runnable but likely wrong.

    Emitted via :func:`warnings.warn` (not raised) so a run still proceeds; tests
    capture it with :func:`pytest.warns`.  Example: a ``loop.max`` back-edge with
    no ``when`` guard (an unconditional N-times loop is usually a mistake).
    """


class WorkflowExecutionError(WorkflowError):
    """Raised when a workflow fails during execution."""


class WorkflowPaused(Exception):
    """Raised when a human node pauses the run awaiting an external signal."""

    def __init__(self, node_id: str, signal: str) -> None:
        super().__init__(f"paused at {node_id!r} awaiting signal {signal!r}")
        self.node_id = node_id
        self.signal = signal


class WorkflowBudgetExceeded(WorkflowError):
    """Raised when a run's cumulative token usage exceeds its configured budget."""

    def __init__(self, used: int, budget: int) -> None:
        super().__init__(f"token budget exceeded: used {used} > budget {budget}")
        self.used = used
        self.budget = budget
