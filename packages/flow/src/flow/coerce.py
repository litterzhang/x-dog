"""flow.coerce — convert between the string-typed workflow state and JSON types.

Workflow state is a flat ``dict[str, str]``.  Typed script nodes declare their
inputs/output with JSON types (``string``/``integer``/``number``/``boolean``/
``array``/``object``); this module converts a stored string INTO the declared
Python type before a script runs (:func:`to_python`) and the script's return
value BACK into a string for storage (:func:`to_state`).
"""

from __future__ import annotations

import json
from typing import Any

VALID_TYPES = ("string", "integer", "number", "boolean", "array", "object")

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off", ""}


def to_python(value: str, json_type: str) -> Any:
    """Coerce a state string *value* into the Python type named by *json_type*."""
    if json_type == "string":
        return value
    if json_type == "integer":
        return int(value) if value.strip() != "" else 0
    if json_type == "number":
        return float(value) if value.strip() != "" else 0.0
    if json_type == "boolean":
        low = value.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"cannot coerce {value!r} to boolean")
    if json_type in ("array", "object"):
        if value.strip() == "":
            return [] if json_type == "array" else {}
        parsed = json.loads(value)
        if json_type == "array" and not isinstance(parsed, list):
            raise ValueError(f"expected a JSON array, got {type(parsed).__name__}")
        if json_type == "object" and not isinstance(parsed, dict):
            raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
        return parsed
    raise ValueError(f"unknown json type {json_type!r}")


def to_state(value: Any, json_type: str) -> str:
    """Coerce a script's return *value* back into a string for state storage."""
    if json_type == "string":
        return value if isinstance(value, str) else str(value)
    if json_type in ("integer", "number", "boolean"):
        return str(value)
    if json_type in ("array", "object"):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    raise ValueError(f"unknown json type {json_type!r}")
