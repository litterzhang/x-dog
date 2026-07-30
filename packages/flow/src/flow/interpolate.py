"""flow.interpolate — template interpolation over the typed workflow wire format.

A template placeholder is ``{{path}}`` where *path* is a dotted sequence of
segments: a bare port name (``{{topic}}``) or a walk into a structured port
value (``{{plan.owner}}``, ``{{plan.tasks.0}}``).  The resolved leaf is projected
to text via :func:`flow.coerce.port_str`, so a structured value renders as
canonical JSON identically in the interpreter and the generated module.

A missing key, out-of-range index, or a walk into a non-container yields the
empty string — the lenient behaviour matching the pre-structure wire format.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from flow.coerce import port_str

# A placeholder is a dotted path: one or more \w+ segments joined by dots.
_PATTERN = re.compile(r"\{\{\s*(\w+(?:\.\w+)*)\s*\}\}")


def resolve_path(value: object, segments: list[str]) -> object:
    """Walk *segments* into a structured *value*; return None on any miss.

    Dict segments index by key; list/tuple segments index by an integer segment.
    A walk into a scalar, a missing key, or an out-of-range index returns None.
    """
    cur = value
    for seg in segments:
        if isinstance(cur, Mapping):
            if seg not in cur:
                return None
            cur = cur[seg]
        elif isinstance(cur, (list, tuple)):
            if not seg.isdigit():
                return None
            idx = int(seg)
            if not 0 <= idx < len(cur):
                return None
            cur = cur[idx]
        else:
            return None
    return cur


def interpolate(template: str, state: Mapping[str, object]) -> str:
    """Replace each ``{{path}}`` with its resolved, projected value.

    ``{{port}}`` projects the whole port value; ``{{port.field}}`` walks into a
    structured value.  Any unresolved path becomes the empty string.
    """

    def _sub(m: re.Match[str]) -> str:
        head, *rest = m.group(1).split(".")
        if head not in state:
            return ""
        resolved = resolve_path(state[head], rest) if rest else state[head]
        if resolved is None:
            return ""
        return port_str(resolved)

    return _PATTERN.sub(_sub, template)
