"""flow.errors — workflow exception hierarchy."""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for all flow workflow errors."""


class WorkflowValidationError(WorkflowError):
    """Raised when a workflow definition fails validation.

    ``code`` names the *kind* of problem, ``node`` / ``edge`` say where it is
    when the check knew, and ``hint`` suggests a repair.  All are optional and
    ``str(exc)`` is unchanged, so nothing that only reads the message is
    affected — they exist so ``xdog-flow validate --json`` can hand a machine
    something better than prose to act on.

    The code matters more than it looks.  Without one, a caller that wants to
    react differently to "this port does not exist" and "these two ports have
    incompatible types" has to pattern-match English, and every reworded
    message is a silent breaking change for them.  The codes are grouped by
    what the author has to *do*, not by where in the loader the check lives,
    because that is the axis a repair loop branches on.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        node: str | None = None,
        edge: tuple[str, str] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.node = node
        self.edge = edge
        self.hint = hint

    def as_dict(self) -> dict[str, object]:
        """A JSON-ready rendering; absent fields are omitted rather than null."""
        payload: dict[str, object] = {"message": str(self)}
        if self.code:
            payload["code"] = self.code
        if self.node is not None:
            payload["node"] = self.node
        if self.edge is not None:
            payload["edge"] = {"from": self.edge[0], "to": self.edge[1]}
        if self.hint is not None:
            payload["hint"] = self.hint
        return payload


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
