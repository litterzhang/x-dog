"""flow.conditions — evaluate Condition trees against workflow state."""

from __future__ import annotations

from collections.abc import Mapping

from flow.errors import WorkflowValidationError
from flow.interpolate import interpolate
from flow.models import Condition


def _as_number(raw: str, op: str) -> float | None:
    """Coerce an interpolated operand to a float.

    An empty operand (an unresolved / not-yet-produced value, e.g. a loop-carried
    port on the first pass) returns None so the caller treats the comparison as
    False — matching interpolation's lenient miss. A non-empty non-numeric operand
    raises, since that is a real authoring error.
    """
    s = raw.strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        raise WorkflowValidationError(
            f"{op!r} requires numeric operands, got {raw!r}"
        ) from None


def evaluate(cond: Condition, state: Mapping[str, object]) -> bool:
    """Evaluate a Condition against state.

    Supported ops:
      equals            — interpolate(value) == interpolate(text)
      contains          — interpolate(text) in interpolate(value)
      gt / gte / lt / lte — numeric compare of interpolate(value) vs interpolate(text)
      not               — not evaluate(children[0])
      and               — all(evaluate(c) for c in children)
      or                — any(evaluate(c) for c in children)

    Raises WorkflowValidationError on unknown op, wrong arity, or non-numeric
    operands to a numeric op.
    """
    if cond.op == "equals":
        if cond.value is None or cond.text is None:
            raise WorkflowValidationError("'equals' requires both 'value' and 'text'")
        return interpolate(cond.value, state) == interpolate(cond.text, state)

    if cond.op == "contains":
        if cond.value is None or cond.text is None:
            raise WorkflowValidationError("'contains' requires both 'value' and 'text'")
        return interpolate(cond.text, state) in interpolate(cond.value, state)

    if cond.op in ("gt", "gte", "lt", "lte"):
        if cond.value is None or cond.text is None:
            raise WorkflowValidationError(f"{cond.op!r} requires both 'value' and 'text'")
        left = _as_number(interpolate(cond.value, state), cond.op)
        right = _as_number(interpolate(cond.text, state), cond.op)
        if left is None or right is None:
            return False  # an unresolved operand — lenient, like interpolation
        if cond.op == "gt":
            return left > right
        if cond.op == "gte":
            return left >= right
        if cond.op == "lt":
            return left < right
        return left <= right

    if cond.op == "not":
        if len(cond.children) != 1:
            raise WorkflowValidationError("'not' requires exactly one child")
        return not evaluate(cond.children[0], state)

    if cond.op == "and":
        return all(evaluate(c, state) for c in cond.children)

    if cond.op == "or":
        return any(evaluate(c, state) for c in cond.children)

    raise WorkflowValidationError(f"Unknown condition op: {cond.op!r}")
