"""Tests for workspace management."""
from xdog.claw.core.prompt import init_workspace, run_bootstrap, set_identity_name


def test_init_workspace_does_not_overwrite_existing(tmp_path):
    ws = tmp_path / "workspace"
    init_workspace(ws)
    (ws / "AGENTS.md").write_text("Custom instructions")
    init_workspace(ws)
    assert (ws / "AGENTS.md").read_text() == "Custom instructions"


def test_set_identity_name_creates_when_missing(tmp_path):
    ws = tmp_path / "workspace"
    set_identity_name(ws, "Claw")
    assert (ws / "IDENTITY.md").read_text() == "# Identity\n\nName: Claw\n"


def test_set_identity_name_overwrites_existing_name(tmp_path):
    """Force-write replaces the Name line but preserves other content.

    Regression for the onboard rename bug: init_workspace seeds IDENTITY.md with
    the default "Assistant" and won't overwrite it, so onboard must force the
    chosen name onto the existing file.
    """
    ws = tmp_path / "workspace"
    init_workspace(ws)  # writes "Name: Assistant"
    (ws / "IDENTITY.md").write_text("# Identity\n\nName: Assistant\n\nExtra: keep me\n")
    set_identity_name(ws, "Claw")
    text = (ws / "IDENTITY.md").read_text()
    assert "Name: Claw" in text
    assert "Name: Assistant" not in text
    assert "Extra: keep me" in text  # unrelated content preserved


def test_set_identity_name_appends_when_no_name_line(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (ws / "IDENTITY.md").write_text("# Identity\n\nSome notes only\n")
    set_identity_name(ws, "Rex")
    text = (ws / "IDENTITY.md").read_text()
    assert "Some notes only" in text
    assert "Name: Rex" in text


def test_run_bootstrap_executes_and_deletes(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (ws / "BOOTSTRAP.md").write_text("First run setup instructions")
    assert run_bootstrap(ws) == "First run setup instructions"
    assert not (ws / "BOOTSTRAP.md").exists()
    assert run_bootstrap(ws) is None
