"""Tests for flow.interpolate and flow.conditions."""

from __future__ import annotations

import pytest
from xdog.flow.conditions import evaluate
from xdog.flow.errors import WorkflowValidationError
from xdog.flow.interpolate import interpolate
from xdog.flow.models import Condition

# ---------------------------------------------------------------------------
# interpolate
# ---------------------------------------------------------------------------


def test_interpolate_basic() -> None:
    assert interpolate("Hello {{name}}!", {"name": "world"}) == "Hello world!"


def test_interpolate_multiple() -> None:
    assert interpolate("{{a}} + {{b}}", {"a": "1", "b": "2"}) == "1 + 2"


def test_interpolate_missing_key() -> None:
    assert interpolate("{{missing}}", {}) == ""


def test_interpolate_whitespace_in_braces() -> None:
    assert interpolate("{{ key }}", {"key": "val"}) == "val"


def test_interpolate_no_placeholders() -> None:
    assert interpolate("plain text", {"key": "val"}) == "plain text"


# ---------------------------------------------------------------------------
# evaluate — equals
# ---------------------------------------------------------------------------


def test_equals_true() -> None:
    cond = Condition(op="equals", value="hello", text="hello")
    assert evaluate(cond, {}) is True


def test_equals_false() -> None:
    cond = Condition(op="equals", value="hello", text="world")
    assert evaluate(cond, {}) is False


def test_equals_interpolated() -> None:
    cond = Condition(op="equals", value="{{x}}", text="foo")
    assert evaluate(cond, {"x": "foo"}) is True
    assert evaluate(cond, {"x": "bar"}) is False


def test_equals_missing_value_raises() -> None:
    cond = Condition(op="equals", text="foo")
    with pytest.raises(WorkflowValidationError):
        evaluate(cond, {})


def test_equals_missing_text_raises() -> None:
    cond = Condition(op="equals", value="foo")
    with pytest.raises(WorkflowValidationError):
        evaluate(cond, {})


# ---------------------------------------------------------------------------
# evaluate — contains
# ---------------------------------------------------------------------------


def test_contains_true() -> None:
    cond = Condition(op="contains", value="hello world", text="world")
    assert evaluate(cond, {}) is True


def test_contains_false() -> None:
    cond = Condition(op="contains", value="hello world", text="xyz")
    assert evaluate(cond, {}) is False


def test_contains_interpolated() -> None:
    cond = Condition(op="contains", value="{{sentence}}", text="{{word}}")
    assert evaluate(cond, {"sentence": "the quick fox", "word": "quick"}) is True
    assert evaluate(cond, {"sentence": "the quick fox", "word": "slow"}) is False


def test_contains_missing_fields_raises() -> None:
    cond = Condition(op="contains", value="foo")
    with pytest.raises(WorkflowValidationError):
        evaluate(cond, {})


# ---------------------------------------------------------------------------
# evaluate — not
# ---------------------------------------------------------------------------


def test_not_inverts() -> None:
    inner = Condition(op="equals", value="a", text="b")
    cond = Condition(op="not", children=(inner,))
    assert evaluate(cond, {}) is True


def test_not_wrong_arity_raises() -> None:
    inner = Condition(op="equals", value="a", text="a")
    cond = Condition(op="not", children=(inner, inner))
    with pytest.raises(WorkflowValidationError):
        evaluate(cond, {})


def test_not_no_children_raises() -> None:
    cond = Condition(op="not")
    with pytest.raises(WorkflowValidationError):
        evaluate(cond, {})


# ---------------------------------------------------------------------------
# evaluate — and
# ---------------------------------------------------------------------------


def test_and_all_true() -> None:
    t = Condition(op="equals", value="a", text="a")
    cond = Condition(op="and", children=(t, t))
    assert evaluate(cond, {}) is True


def test_and_one_false() -> None:
    t = Condition(op="equals", value="a", text="a")
    f = Condition(op="equals", value="a", text="b")
    cond = Condition(op="and", children=(t, f))
    assert evaluate(cond, {}) is False


def test_and_empty_children() -> None:
    # all([]) is True
    cond = Condition(op="and")
    assert evaluate(cond, {}) is True


# ---------------------------------------------------------------------------
# evaluate — or
# ---------------------------------------------------------------------------


def test_or_one_true() -> None:
    t = Condition(op="equals", value="a", text="a")
    f = Condition(op="equals", value="a", text="b")
    cond = Condition(op="or", children=(t, f))
    assert evaluate(cond, {}) is True


def test_or_all_false() -> None:
    f = Condition(op="equals", value="a", text="b")
    cond = Condition(op="or", children=(f, f))
    assert evaluate(cond, {}) is False


def test_or_empty_children() -> None:
    # any([]) is False
    cond = Condition(op="or")
    assert evaluate(cond, {}) is False


# ---------------------------------------------------------------------------
# nested conditions
# ---------------------------------------------------------------------------


def test_nested_and_or() -> None:
    # (a==a OR a==b) AND (a==a)
    t = Condition(op="equals", value="a", text="a")
    f = Condition(op="equals", value="a", text="b")
    inner_or = Condition(op="or", children=(t, f))
    cond = Condition(op="and", children=(inner_or, t))
    assert evaluate(cond, {}) is True


def test_nested_not_and() -> None:
    # NOT (a==a AND a==b) -> NOT False -> True
    t = Condition(op="equals", value="a", text="a")
    f = Condition(op="equals", value="a", text="b")
    inner_and = Condition(op="and", children=(t, f))
    cond = Condition(op="not", children=(inner_and,))
    assert evaluate(cond, {}) is True


def test_nested_interpolation_in_children() -> None:
    # state-driven nested: ({{x}}==foo) AND ({{y}} contains bar)
    eq = Condition(op="equals", value="{{x}}", text="foo")
    contains = Condition(op="contains", value="{{y}}", text="bar")
    cond = Condition(op="and", children=(eq, contains))
    assert evaluate(cond, {"x": "foo", "y": "foobar"}) is True
    assert evaluate(cond, {"x": "foo", "y": "baz"}) is False


# ---------------------------------------------------------------------------
# numeric ops (gt / gte / lt / lte)
# ---------------------------------------------------------------------------


def test_numeric_gte_true_and_false() -> None:
    assert evaluate(Condition(op="gte", value="{{score}}", text="0.8"), {"score": 0.85}) is True
    assert evaluate(Condition(op="gte", value="{{score}}", text="0.8"), {"score": 0.8}) is True
    assert evaluate(Condition(op="gte", value="{{score}}", text="0.8"), {"score": 0.79}) is False


def test_numeric_gt_lt_lte() -> None:
    st = {"n": 5}
    assert evaluate(Condition(op="gt", value="{{n}}", text="4"), st) is True
    assert evaluate(Condition(op="gt", value="{{n}}", text="5"), st) is False
    assert evaluate(Condition(op="lt", value="{{n}}", text="6"), st) is True
    assert evaluate(Condition(op="lte", value="{{n}}", text="5"), st) is True


def test_numeric_ignores_string_formatting() -> None:
    # 0.80 vs 0.8 — numeric compare, not string compare
    assert evaluate(Condition(op="gte", value="{{s}}", text="0.80"), {"s": 0.8}) is True
    assert evaluate(Condition(op="equals", value="{{s}}", text="0.80"), {"s": 0.8}) is False


def test_numeric_non_numeric_operand_raises() -> None:
    with pytest.raises(WorkflowValidationError, match="numeric operands"):
        evaluate(Condition(op="gt", value="hello", text="0.8"), {})


# ---------------------------------------------------------------------------
# unknown op
# ---------------------------------------------------------------------------


def test_unknown_op_raises() -> None:
    # Condition.op is typed but we bypass at runtime
    cond = Condition(op="equals", value="a", text="a")
    from dataclasses import replace

    bad_cond = replace(cond, op="xor")  # type: ignore[arg-type]
    with pytest.raises(WorkflowValidationError, match="Unknown condition op"):
        evaluate(bad_cond, {})
