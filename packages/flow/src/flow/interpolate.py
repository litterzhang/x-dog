"""flow.interpolate — JSONPath template interpolation over the workflow wire format.

A template placeholder is ``{{ <jsonpath> }}`` where ``<jsonpath>`` is a JSONPath
expression evaluated against the node's state dict — a bare port
(``{{ $.topic }}``) or a walk into a structured port value
(``{{ $.plan.owner }}``, ``{{ $.plan.tasks[0] }}``).  The resolved leaf is
projected to text via :func:`flow.coerce.port_str`, so a structured value renders
as canonical JSON identically in the interpreter and the generated module.

A path with no match yields the empty string — the lenient behaviour matching the
pre-JSONPath wire format.  The same :func:`jsonpath_get` helper is inlined into
the generated module, so both engines resolve paths identically.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from jsonpath_ng import parse as _jsonpath_parse

from flow.coerce import port_str

# A placeholder is ``{{ <jsonpath> }}`` — any expression between the braces.
_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}")

# Cache compiled JSONPath expressions by their string form (parsing is not cheap).
_JSONPATH_CACHE: dict[str, Any] = {}


def jsonpath_get(state: object, path: str) -> object:
    """Return the first JSONPath *path* match in *state*, or None if none.

    A bare field (``$.topic`` / ``topic``) or a nested walk (``$.plan.tasks[0]``)
    are both supported.  Compiled expressions are cached by their string.
    """
    expr = _JSONPATH_CACHE.get(path)
    if expr is None:
        expr = _jsonpath_parse(path)
        _JSONPATH_CACHE[path] = expr
    matches = expr.find(state)
    return matches[0].value if matches else None


def interpolate(template: str, state: Mapping[str, object]) -> str:
    """Replace each ``{{ <jsonpath> }}`` with its resolved, projected value.

    ``{{ $.port }}`` projects the whole port value; ``{{ $.port.field }}`` walks
    into a structured value.  Any unresolved path becomes the empty string.
    """

    def _sub(m: re.Match[str]) -> str:
        resolved = jsonpath_get(state, m.group(1))
        if resolved is None:
            return ""
        return port_str(resolved)

    return _PATTERN.sub(_sub, template)
