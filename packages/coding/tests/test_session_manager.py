import time
from pathlib import Path

from xdog.coding.core.session_manager import SessionManager


def test_session_manager_crud(tmp_path: Path):
    manager = SessionManager(sessions_dir=tmp_path)

    # Create
    session = manager.create_session(model="test-model", summary="test summary")
    assert session.session_id
    assert session.model == "test-model"
    assert session.summary == "test summary"

    # Load
    loaded = manager.load_session(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.model == "test-model"

    # Update
    loaded.summary = "new summary"
    manager.save_session(loaded)

    loaded2 = manager.load_session(session.session_id)
    assert loaded2 is not None
    assert loaded2.summary == "new summary"

    # List
    sessions = manager.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == session.session_id

    # Delete
    assert manager.delete_session(session.session_id) is True
    assert manager.load_session(session.session_id) is None
    assert len(manager.list_sessions()) == 0

def test_session_manager_most_recent(tmp_path: Path):
    manager = SessionManager(sessions_dir=tmp_path)

    manager.create_session(summary="old")
    time.sleep(0.01)
    s2 = manager.create_session(summary="new")

    recent = manager.get_most_recent()
    assert recent is not None
    assert recent.session_id == s2.session_id
    assert recent.summary == "new"
