"""flow.runtime — the runtime context passed to script nodes.

A script node's function is called as ``fn(ctx, **inputs)`` where the declared
input **ports** arrive as keyword arguments (by port name, typed).  *ctx* is a
:class:`RuntimeContext` giving the script the wider picture: the same port-local
inputs as a mapping, plus which node/workflow it is running in.  Kept small and
frozen; extend as new needs appear.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeContext:
    """Read-only view of the workflow for a single script-node invocation."""

    inputs: Mapping[str, str]
    """This node's input ports (port name -> string value), before typed coercion."""

    workflow_name: str
    """The ``name`` of the workflow being executed."""

    node_id: str
    """The id of the script node currently running."""
