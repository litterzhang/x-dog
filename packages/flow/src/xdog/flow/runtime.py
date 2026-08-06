"""flow.runtime — the runtime context passed to script nodes.

A script node's function is called as ``fn(ctx, **inputs)`` where the declared
input **ports** arrive as keyword arguments (by port name, typed).  *ctx* is a
:class:`RuntimeContext` carrying only **runtime information** about the current
execution — the step number, which node is running, and the workflow name.  It
deliberately does NOT hold the run's input data: a script reads its inputs from
the typed keyword arguments, not from *ctx*.  Kept small and frozen; extend as
new needs appear.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeContext:
    """Read-only runtime info for a single script-node invocation."""

    step: int
    """Zero-based execution sequence number (how many nodes ran before this one)."""

    node_id: str
    """The id of the script node currently running."""

    workflow_name: str
    """The ``name`` of the workflow being executed."""
