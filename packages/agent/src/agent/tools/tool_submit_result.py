"""submit_result tool — an agent submits its final structured result.

The agent calls ``submit_result`` with a structured object.  The tool validates
that object against a JSON schema supplied by the caller through the shared
``tool_ctx`` dict (key ``flow_output_schema``).  On success it writes the
validated object into a caller-owned **sink** dict (``tool_ctx`` key
``flow_result_sink``) under ``"result"``, and returns an acceptance message.
On failure it returns an ``Error:`` string, which the agent sees as the tool
result and can act on by fixing and calling again.

Why a sink instead of writing a new ctx key: the :class:`~agent.tool_def.ToolDef`
dispatch passes the handler a **shallow copy** of ``tool_ctx`` (``dict(ctx or
{})``), so adding a new key would not reach the caller.  A shallow copy still
shares references to *values*, so mutating a caller-provided ``flow_result_sink``
dict is visible to the caller after the agent turn drains.

The schema is a flat mapping ``{field_name: json_type}`` where ``json_type`` is
one of ``string``, ``integer``, ``number``, ``boolean``, ``array``, ``object``.
Validation is intentionally minimal and dependency-free (no ``jsonschema``):
every declared field must be present with a matching JSON type.  Extra fields
are allowed.  When no schema is supplied, any object is accepted.
"""

from __future__ import annotations

from typing import Any

from agent.core import AgentTool
from agent.tool_def import Param, ToolDef

# JSON-schema type name -> the Python type(s) that satisfy it.
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _looks_like_json_schema(schema: dict[str, Any]) -> bool:
    """True if *schema* is a JSON Schema (has ``type``/``properties``) vs the
    legacy flat ``{field: json_type}`` mapping."""
    return "type" in schema or "properties" in schema


def _validate_against_schema(result: Any, schema: Any) -> str | None:
    """Return an error string if *result* violates *schema*, else ``None``.

    *schema* is either a real JSON Schema (validated with fastjsonschema) or the
    legacy flat ``{field: json_type}`` mapping (presence + type of declared
    fields; extra fields pass).  A falsy schema accepts anything.
    """
    if not schema:
        return None
    if not isinstance(schema, dict):
        return "invalid schema (expected an object)"

    if _looks_like_json_schema(schema):
        import fastjsonschema

        try:
            fastjsonschema.compile(schema)(result)
        except fastjsonschema.JsonSchemaException as exc:
            return str(exc)
        except ValueError as exc:  # invalid schema — don't block the agent on our bug
            return f"invalid schema: {exc}"
        return None

    # Legacy flat {field: json_type} mapping.
    if not isinstance(result, dict):
        return f"result must be an object, got {type(result).__name__}"
    for field, json_type in schema.items():
        if field not in result:
            return f"missing required field {field!r}"
        expected = _JSON_TYPES.get(json_type)
        if expected is None:
            # Unknown type in the schema — don't block the agent on our own bug.
            continue
        value = result[field]
        # bool is a subclass of int; reject it where a number/integer is wanted.
        if json_type in ("integer", "number") and isinstance(value, bool):
            return f"field {field!r} must be {json_type}, got boolean"
        if not isinstance(value, expected):
            return f"field {field!r} must be {json_type}, got {type(value).__name__}"
    return None


class SubmitResultTool(ToolDef):
    """Single-action tool for submitting a schema-validated structured result."""

    name = "submit_result"
    description = (
        "Submit your final structured result as an object. It is validated "
        "against the required schema; if validation fails you receive an error "
        "and must fix the object and call submit_result again."
    )
    params = {
        "result": Param(
            "object",
            required=True,
            description="The structured result object to submit.",
        ),
    }

    async def execute(self, ctx: dict[str, Any] | None, result: Any) -> str:
        ctx = ctx or {}
        schema = ctx.get("flow_output_schema")
        error = _validate_against_schema(result, schema)
        if error is not None:
            return f"Error: {error}"
        sink = ctx.get("flow_result_sink")
        if isinstance(sink, dict):
            sink["result"] = result
        return "Result accepted."


def create_submit_result_tool() -> AgentTool:
    """Build the ``submit_result`` :class:`AgentTool`."""
    return SubmitResultTool().build()
