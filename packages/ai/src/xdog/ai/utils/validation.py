"""Tool argument validation using JSON Schema (via pydantic).

Validates that the arguments dict returned by the LLM matches the JSON
schema declared by the tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating tool arguments."""

    valid: bool
    errors: tuple[str, ...] = ()


def validate_tool_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any],
) -> ValidationResult:
    """Validate *arguments* against a JSON Schema *schema*.

    This performs a lightweight structural check.  The *schema* is expected
    to follow the subset of JSON Schema typically used by LLM tool
    definitions (``type: "object"`` with ``properties`` and optionally
    ``required``).

    Parameters
    ----------
    arguments:
        The argument dict to validate (e.g. from a tool call).
    schema:
        The JSON Schema for the tool's parameters.

    Returns
    -------
    ValidationResult
        ``valid=True`` when no errors are found.
    """
    errors: list[str] = []
    properties = schema.get("properties", {})
    required_keys = set(schema.get("required", []))

    # Check required keys are present.
    for key in required_keys:
        if key not in arguments:
            errors.append(f"Missing required argument: {key!r}")

    # Check types where possible.
    _TYPE_MAP = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    for key, value in arguments.items():
        prop_schema = properties.get(key)
        if prop_schema is None:
            continue  # Extra keys are tolerated.

        expected_type_name = prop_schema.get("type")
        if expected_type_name is None:
            continue

        expected_types = _TYPE_MAP.get(expected_type_name)
        if expected_types is None:
            continue

        if not isinstance(value, expected_types):  # type: ignore[arg-type]  # runtime-built tuple of types
            errors.append(
                f"Argument {key!r}: expected {expected_type_name}, "
                f"got {type(value).__name__}"
            )

        # Enum check
        enum_values = prop_schema.get("enum")
        if enum_values is not None and value not in enum_values:
            errors.append(
                f"Argument {key!r}: value {value!r} not in "
                f"allowed values {enum_values}"
            )

    if errors:
        return ValidationResult(valid=False, errors=tuple(errors))
    return ValidationResult(valid=True)
