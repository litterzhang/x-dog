"""Tests for flow.coerce — wire-value <-> JSON type conversions.

The wire format is type-native: ``to_state`` returns the stored Python value
(int/float/bool/list/dict), NOT a string.  ``port_str`` is the canonical string
projection used when a value must become text.
"""

from __future__ import annotations

import pytest
from flow.coerce import port_str, to_python, to_state


def test_string_roundtrip() -> None:
    assert to_python("hello", "string") == "hello"
    assert to_state("hello", "string") == "hello"


def test_integer() -> None:
    assert to_python("3", "integer") == 3
    assert isinstance(to_python("3", "integer"), int)
    assert to_state(7, "integer") == 7
    assert isinstance(to_state(7, "integer"), int)
    # the key property: 3 + 4 = 7, not "34"
    assert to_python("3", "integer") + to_python("4", "integer") == 7
    # tolerant: an already-typed value passes through
    assert to_python(3, "integer") == 3


def test_number() -> None:
    assert to_python("2.5", "number") == 2.5
    assert to_state(2.5, "number") == 2.5
    assert isinstance(to_state(2.5, "number"), float)
    assert to_python(2.5, "number") == 2.5


def test_boolean() -> None:
    assert to_python("true", "boolean") is True
    assert to_python("false", "boolean") is False
    assert to_python("1", "boolean") is True
    assert to_python("", "boolean") is False
    assert to_state(True, "boolean") is True
    assert to_python(True, "boolean") is True


def test_boolean_invalid() -> None:
    with pytest.raises(ValueError):
        to_python("maybe", "boolean")


def test_array() -> None:
    assert to_python('[1, 2, 3]', "array") == [1, 2, 3]
    assert to_python("", "array") == []
    # to_state keeps structure — a live list, not a JSON string
    assert to_state([1, 2], "array") == [1, 2]
    assert isinstance(to_state([1, 2], "array"), list)
    # tolerant: a JSON string is still accepted and parsed
    assert to_state('[1, 2]', "array") == [1, 2]


def test_object() -> None:
    assert to_python('{"a": 1}', "object") == {"a": 1}
    assert to_python("", "object") == {}
    # to_state keeps structure — a live dict, not a JSON string
    assert to_state({"a": 1}, "object") == {"a": 1}
    assert isinstance(to_state({"a": 1}, "object"), dict)
    assert to_python({"a": 1}, "object") == {"a": 1}


def test_port_str_projection() -> None:
    # strings pass through verbatim (no JSON quoting)
    assert port_str("hi") == "hi"
    # structured values render as canonical sorted-key JSON, identical in both engines
    assert port_str({"b": 2, "a": 1}) == '{"a": 1, "b": 2}'
    assert port_str([1, 2]) == "[1, 2]"
    assert port_str(42) == "42"
    assert port_str(True) == "true"


def test_array_wrong_json_shape() -> None:
    with pytest.raises(ValueError):
        to_python('{"a": 1}', "array")


def test_unknown_type() -> None:
    with pytest.raises(ValueError):
        to_python("x", "nope")
    with pytest.raises(ValueError):
        to_state("x", "nope")
