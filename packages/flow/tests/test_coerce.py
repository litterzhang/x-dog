"""Tests for flow.coerce — state string <-> JSON type conversions."""

from __future__ import annotations

import pytest
from flow.coerce import to_python, to_state


def test_string_roundtrip() -> None:
    assert to_python("hello", "string") == "hello"
    assert to_state("hello", "string") == "hello"


def test_integer() -> None:
    assert to_python("3", "integer") == 3
    assert isinstance(to_python("3", "integer"), int)
    assert to_state(7, "integer") == "7"
    # the key property: 3 + 4 = 7, not "34"
    assert to_python("3", "integer") + to_python("4", "integer") == 7


def test_number() -> None:
    assert to_python("2.5", "number") == 2.5
    assert to_state(2.5, "number") == "2.5"


def test_boolean() -> None:
    assert to_python("true", "boolean") is True
    assert to_python("false", "boolean") is False
    assert to_python("1", "boolean") is True
    assert to_python("", "boolean") is False
    assert to_state(True, "boolean") == "True"


def test_boolean_invalid() -> None:
    with pytest.raises(ValueError):
        to_python("maybe", "boolean")


def test_array() -> None:
    assert to_python('[1, 2, 3]', "array") == [1, 2, 3]
    assert to_python("", "array") == []
    assert to_state([1, 2], "array") == "[1, 2]"


def test_object() -> None:
    assert to_python('{"a": 1}', "object") == {"a": 1}
    assert to_python("", "object") == {}
    assert to_state({"a": 1}, "object") == '{"a": 1}'


def test_array_wrong_json_shape() -> None:
    with pytest.raises(ValueError):
        to_python('{"a": 1}', "array")


def test_unknown_type() -> None:
    with pytest.raises(ValueError):
        to_python("x", "nope")
    with pytest.raises(ValueError):
        to_state("x", "nope")
