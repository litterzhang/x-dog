"""flow.runtime — the runtime context passed to script nodes.

A script node's function is called as ``fn(ctx, **inputs)`` where *ctx* is a
:class:`RuntimeContext` giving read access to the running workflow.  The declared
``inputs`` arrive as keyword arguments (by name from state); ``ctx`` is there for
scripts that also need the wider picture (the full state snapshot, which node they
are, which workflow).  Kept small and frozen; extend as new needs appear.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeContext:
    """Read-only view of the workflow for a single script-node invocation."""

    state: Mapping[str, str]
    """Snapshot of the full workflow state at the moment this node runs."""

    workflow_name: str
    """The ``name`` of the workflow being executed."""

    node_id: str
    """The id of the script node currently running."""
