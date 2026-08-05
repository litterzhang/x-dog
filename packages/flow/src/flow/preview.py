"""flow.preview — lossy one-line renderings of a node's ports for logs and events.

A run tells you which nodes ran and how long they took, but not what moved
between them.  When a scheduled workflow misbehaves at 4am the first question is
always "what did that node actually see, and what did it say back", and the trace
that answers it lives in memory and is gone.

These renderings are deliberately lossy and deliberately *identical* in both
engines: the interpreter attaches them to lifecycle events, and codegen inlines
this exact source so a generated module's ``-v`` log says the same thing.  They
are for reading, never for parsing — the authoritative values are the run
result's ``stack`` frames.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping

# Characters kept per port value before eliding. Wide enough for a path, a short
# verdict or the head of a sentence; narrow enough that eight of them still fit a
# terminal-ish line.
_VALUE_LIMIT = 96

# Ports rendered before the rest are summarised as a count. Nodes with more than
# a handful of ports are usually hydrators whose payload is not the interesting
# part of the line.
_MAX_PORTS = 8

# Hard backstop on the whole rendering, independent of the two limits above.
_LINE_LIMIT = 600


def _elide(text: str, limit: int) -> str:
    """*text* clipped to *limit*, with a count of what was dropped."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…(+{len(text) - limit})"


def _render_value(value: object) -> str:
    """One flat line for a single port value.

    Strings are shown raw — quoting them would double the escaping on the exact
    values (prompts, diffs, verdicts) that are already the longest. Everything
    else goes through compact JSON so a list stays visibly a list, with ``str``
    as the fallback for values JSON cannot take.
    """
    if isinstance(value, str):
        flat = " ".join(value.split())
    else:
        try:
            flat = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            flat = str(value)
        flat = " ".join(flat.split())
    return _elide(flat, _VALUE_LIMIT)


def preview_ports(ports: Mapping[str, object]) -> str:
    """``"repo=/srv/app ruff_baseline=0"`` — a node's ports on one readable line.

    Returns ``""`` for no ports, so a caller can drop the segment entirely rather
    than log an empty one.
    """
    if not ports:
        return ""
    names = list(ports)
    shown = names[:_MAX_PORTS]
    parts = [f"{name}={_render_value(ports[name])}" for name in shown]
    if len(names) > _MAX_PORTS:
        parts.append(f"…(+{len(names) - _MAX_PORTS} more)")
    return _elide(" ".join(parts), _LINE_LIMIT)


_INLINE_FUNCTIONS = (_elide, _render_value, preview_ports)


def render_preview_runtime() -> str:
    """Return the exact port-preview helpers for standalone generated modules."""
    constants = (
        f"_VALUE_LIMIT = {_VALUE_LIMIT}\n"
        f"_MAX_PORTS = {_MAX_PORTS}\n"
        f"_LINE_LIMIT = {_LINE_LIMIT}\n"
    )
    return constants + "\n\n".join(inspect.getsource(fn) for fn in _INLINE_FUNCTIONS)
