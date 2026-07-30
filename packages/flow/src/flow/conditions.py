"""flow.conditions — evaluate Condition trees against workflow state."""

from __future__ import annotations

from collections.abc import Mapping

from flow.errors import WorkflowValidationError
from flow.interpolate import interpolate
from flow.models import Condition


def evaluate(cond: Condition, state: Mapping[str, object]) -> bool:
    """Evaluate a Condition against state.

    Supported ops:
      equals   — interpolate(value) == interpolate(text)
      contains — interpolate(text) in interpolate(value)
      not      — not evaluate(children[0])
      and      — all(evaluate(c) for c in children)
      or       — any(evaluate(c) for c in children)

    Raises WorkflowValidationError on unknown op or wrong arity.
    """
    if cond.op == "equals":
        if cond.value is None or cond.text is None:
            raise WorkflowValidationError("'equals' requires both 'value' and 'text'")
        return interpolate(cond.value, state) == interpolate(cond.text, state)

    if cond.op == "contains":
        if cond.value is None or cond.text is None:
            raise WorkflowValidationError("'contains' requires both 'value' and 'text'")
        return interpolate(cond.text, state) in interpolate(cond.value, state)

    if cond.op == "not":
        if len(cond.children) != 1:
            raise WorkflowValidationError("'not' requires exactly one child")
        return not evaluate(cond.children[0], state)

    if cond.op == "and":
        return all(evaluate(c, state) for c in cond.children)

    if cond.op == "or":
        return any(evaluate(c, state) for c in cond.children)

    raise WorkflowValidationError(f"Unknown condition op: {cond.op!r}")
