"""Tests for XDG config path resolution in coding.config."""

from pathlib import Path

import pytest
from xdog.coding.config import (
    get_config_dir,
    get_data_dir,
    get_state_dir,
)


def test_env_override_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CODING_DIR overrides all XDG paths to a single directory."""
    override = tmp_path / "override"
    monkeypatch.setenv("CODING_DIR", str(override))

    assert get_config_dir() == override
    assert get_data_dir() == override
    assert get_state_dir() == override
