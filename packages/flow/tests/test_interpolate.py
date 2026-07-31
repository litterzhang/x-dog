"""Tests for flow.interpolate — JSONPath interpolation over typed wire values."""

from __future__ import annotations

from flow.interpolate import interpolate, jsonpath_get


def test_bare_string_port() -> None:
    assert interpolate("hi {{$.name}}", {"name": "ada"}) == "hi ada"


def test_missing_key_is_empty() -> None:
    assert interpolate("[{{$.nope}}]", {"name": "ada"}) == "[]"


def test_whole_structured_port_projects_canonical_json() -> None:
    # A structured port renders as sorted-key JSON (matches codegen projection).
    assert interpolate("{{$.plan}}", {"plan": {"b": 2, "a": 1}}) == '{"a": 1, "b": 2}'


def test_nested_object_field() -> None:
    state = {"plan": {"owner": "ada", "tasks": ["spec", "ship"]}}
    assert interpolate("owner={{$.plan.owner}}", state) == "owner=ada"


def test_nested_list_index() -> None:
    state = {"plan": {"tasks": ["spec", "ship"]}}
    assert interpolate("first={{$.plan.tasks[0]}}", state) == "first=spec"
    assert interpolate("last={{$.plan.tasks[1]}}", state) == "last=ship"


def test_missing_nested_field_is_empty() -> None:
    state = {"plan": {"owner": "ada"}}
    assert interpolate("[{{$.plan.missing}}]", state) == "[]"
    assert interpolate("[{{$.plan.tasks[5]}}]", state) == "[]"


def test_walk_into_scalar_is_empty() -> None:
    # $.name.field where name is a scalar -> no match -> empty, not an error.
    assert interpolate("[{{$.name.field}}]", {"name": "ada"}) == "[]"


def test_scalar_ports_project() -> None:
    assert interpolate("{{$.n}}+{{$.f}}={{$.b}}", {"n": 3, "f": 2.5, "b": True}) == "3+2.5=true"


def test_jsonpath_get_helper() -> None:
    v = {"a": {"b": [10, 20]}}
    assert jsonpath_get(v, "$.a.b[1]") == 20
    assert jsonpath_get(v, "$.a.x") is None
    assert jsonpath_get(v, "$.a.b[9]") is None
    assert jsonpath_get(42, "$.a") is None
