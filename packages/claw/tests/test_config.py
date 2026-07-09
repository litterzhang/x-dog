"""Tests for claw YAML configuration loading."""
import pytest
from pathlib import Path
from claw.config import ClawConfig, GroupDef, load_config, save_config

def test_default_config():
    config = ClawConfig()
    assert config.max_concurrent_agents == 3
    assert config.model == "copilot/claude-sonnet-4.5"
    assert config.groups == ()
    # Frozen
    with pytest.raises(AttributeError):
        config.model = "changed"

def test_load_config_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: copilot/claude-opus-4\n"
        "data_dir: /tmp/test-data\n"
        "gateway:\n"
        "  max_concurrent_agents: 5\n"
        "  daily_reset_hour: 6\n"
        "groups:\n"
        "  main:\n"
        "    name: TestClaw\n"
        "    is_main: true\n"
        "  helper:\n"
        "    name: Helper\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.model == "copilot/claude-opus-4"
    assert config.data_dir == "/tmp/test-data"
    assert config.max_concurrent_agents == 5
    assert len(config.groups) == 2
    assert config.groups[0] == GroupDef(id="main", name="TestClaw", is_main=True)

def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "nested" / "config.yaml"
    original = ClawConfig(
        model="test-model",
        data_dir="/custom/data",
        max_concurrent_agents=5,
        groups=(GroupDef(id="main", name="Main", is_main=True),),
    )
    save_config(original, path)
    assert path.exists()

    loaded = load_config(path)
    assert loaded.model == original.model
    assert loaded.data_dir == original.data_dir
    assert loaded.max_concurrent_agents == original.max_concurrent_agents
    assert len(loaded.groups) == 1
