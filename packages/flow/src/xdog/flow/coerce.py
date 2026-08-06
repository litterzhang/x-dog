"""flow.coerce — convert between the typed workflow wire format and JSON types.

Workflow state is a flat ``dict[str, object]``: a port value is the type-native
Python value named by its JSON type (``string`` → str, ``integer`` → int,
``number`` → float, ``boolean`` → bool, ``array`` → list, ``object`` → dict).

Three conversions keep the interpreter and the generated module in agreement:

* :func:`to_python` — the stored wire value INTO the Python value a script sees.
  An already-typed value passes through; string inputs from workflow state or CLI
  overrides are parsed according to the destination port schema.
* :func:`to_state` — a script's return value INTO the stored wire value, keeping
  structure (an ``object`` port stores a live dict, not a JSON string).
* :func:`port_str` — the canonical string projection used whenever a structured
  value must become text (prompt interpolation, string conditions).  Both engines
  call the SAME projection, so ``interpret == compile`` holds under structure.
"""

from __future__ import annotations

import json
from typing import Any

VALID_TYPES = ("string", "integer", "number", "boolean", "array", "object")

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off", ""}


def port_str(value: object) -> str:
    """Project a stored port *value* to its canonical string form.

    A ``str`` is returned verbatim (no JSON quoting), so a plain string port
    interpolates as-is.  Any structured value is rendered as canonical,
    sorted-key JSON — identical in the interpreter and the generated module.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _to_bool(value: object) -> bool:
    """Coerce a bool-or-string *value* to bool via the truthy/falsey word sets."""
    if isinstance(value, bool):
        return value
    low = str(value).strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    raise ValueError(f"cannot coerce {value!r} to boolean")


def to_python(value: object, json_type: str) -> Any:
    """Coerce a stored wire *value* into the Python type named by *json_type*.

    Accepts type-native values and parses string inputs supplied through workflow
    state or CLI overrides according to the destination schema.
    """
    if json_type == "string":
        return value if isinstance(value, str) else port_str(value)
    if json_type == "integer":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        s = str(value).strip()
        return int(s) if s != "" else 0
    if json_type == "number":
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s != "" else 0.0
    if json_type == "boolean":
        return _to_bool(value)
    if json_type in ("array", "object"):
        if isinstance(value, str):
            if value.strip() == "":
                return [] if json_type == "array" else {}
            value = json.loads(value)
        if isinstance(value, tuple):
            value = list(value)
        if json_type == "array" and not isinstance(value, list):
            raise ValueError(f"expected a JSON array, got {type(value).__name__}")
        if json_type == "object" and not isinstance(value, dict):
            raise ValueError(f"expected a JSON object, got {type(value).__name__}")
        return value
    raise ValueError(f"unknown json type {json_type!r}")


def to_state(value: object, json_type: str) -> object:
    """Coerce a script's return *value* into the stored wire value (type-native).

    Structure is preserved: an ``array``/``object`` port stores a live list/dict,
    not a JSON string.  Scalars are normalised to their canonical Python type.
    """
    if json_type == "string":
        return value if isinstance(value, str) else port_str(value)
    if json_type == "integer":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return int(str(value).strip() or "0")
    if json_type == "number":
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).strip() or "0")
    if json_type == "boolean":
        return _to_bool(value)
    if json_type in ("array", "object"):
        if isinstance(value, str):
            value = json.loads(value) if value.strip() != "" else ([] if json_type == "array" else {})
        if isinstance(value, tuple):
            value = list(value)
        if json_type == "array" and not isinstance(value, list):
            raise ValueError(f"expected a JSON array, got {type(value).__name__}")
        if json_type == "object" and not isinstance(value, dict):
            raise ValueError(f"expected a JSON object, got {type(value).__name__}")
        return value
    raise ValueError(f"unknown json type {json_type!r}")
