"""Smoke tests for the flow package."""

from __future__ import annotations

import xdog.flow as flow


def test_import() -> None:
    assert flow is not None


def test_all_is_list() -> None:
    assert isinstance(flow.__all__, list)
