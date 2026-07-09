import pytest
from pathlib import Path
import json
from coding.core.settings_manager import SettingsManager


def test_settings_manager_hierarchy(tmp_path: Path):
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({
        "default_model": "global_model",
        "thinking_level": "normal",
        "custom_instructions": "Global instruct"
    }))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".coding").mkdir()
    (project_dir / ".coding" / "settings.json").write_text(json.dumps({
        "model": "project_model",
        "custom_instructions": "Project instruct"
    }))

    manager = SettingsManager(global_path=global_path, project_dir=project_dir)

    # Project overrides global
    assert manager.model == "project_model"
    # Fallback to global
    assert manager.thinking_level == "normal"
    # Instructions concatenated
    assert "Global instruct" in manager.custom_instructions
    assert "Project instruct" in manager.custom_instructions

    # Session overrides
    manager.set_session_model("session_model")
    manager.set_session_thinking("deep")
    manager.set_session_instructions("Session instruct")

    assert manager.model == "session_model"
    assert manager.thinking_level == "deep"
    assert "Session instruct" in manager.custom_instructions


def test_session_serialization():
    manager = SettingsManager()
    manager.set_session_model("test_model")
    manager.set_session_thinking("deep")

    data = manager.session_to_dict()
    assert data["model"] == "test_model"
    assert data["thinking_level"] == "deep"

    manager2 = SettingsManager()
    manager2.load_session_settings(data)
    assert manager2.model == "test_model"
    assert manager2.thinking_level == "deep"
