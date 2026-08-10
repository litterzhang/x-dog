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

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RuntimeContext:
    """Read-only runtime info for a single script-node invocation."""

    step: int
    """Zero-based execution sequence number (how many nodes ran before this one)."""

    node_id: str
    """The id of the script node currently running."""

    workflow_name: str
    """The ``name`` of the workflow being executed."""

    workspace: Path | None = None
    """Where this run's files belong, or ``None`` when the caller set no workspace.

    A script node is held to this bound by an audit hook, so it has to be able to
    find out what the bound *is* — being refused for writing outside a directory
    whose path you were never given is not a rule, it is a trap. Relative paths
    are no help either: nodes run concurrently, so the executor cannot ``chdir``
    into the workspace on a script's behalf without corrupting its siblings.

    Use it: ``(ctx.workspace / "report.md").write_text(...)``.
    """

    allow_paths: tuple[Path, ...] = field(default_factory=tuple)
    """Directories granted in addition to the workspace, by ``--allow-path``."""

    confined: bool = False
    """True when this run also refuses calls the audit hook cannot follow.

    A script that shells out or loads a C library will be stopped rather than
    quietly escaping the bound, so a script with an optional fast path can check
    this and take the auditable branch instead of failing.
    """
