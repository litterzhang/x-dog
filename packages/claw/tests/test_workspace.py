"""Tests for workspace management."""
from claw.core.prompt import init_workspace, run_bootstrap


def test_init_workspace_does_not_overwrite_existing(tmp_path):
    ws = tmp_path / "workspace"
    init_workspace(ws)
    (ws / "AGENTS.md").write_text("Custom instructions")
    init_workspace(ws)
    assert (ws / "AGENTS.md").read_text() == "Custom instructions"

def test_run_bootstrap_executes_and_deletes(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (ws / "BOOTSTRAP.md").write_text("First run setup instructions")
    assert run_bootstrap(ws) == "First run setup instructions"
    assert not (ws / "BOOTSTRAP.md").exists()
    assert run_bootstrap(ws) is None
