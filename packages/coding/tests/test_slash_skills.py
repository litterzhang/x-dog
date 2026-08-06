"""Tests for skills exposed as slash commands.

The wiring that matters is not that `/flow` prints something — it is that the
skill's text reaches the model, and that it can be taken back out again. The
reference clients for this format put the body into the message history, where
it stays for the rest of the session; these tests pin the alternative.
"""
from pathlib import Path
from typing import Any

import pytest
from xdog.agent.skills import SkillManager
from xdog.coding.core import slash_commands
from xdog.coding.core.slash_commands import (
    BUILTIN_COMMANDS,
    execute_command,
    list_commands,
    parse_slash_command,
)


class FakeSession:
    """Just the surface the skill commands touch."""

    def __init__(self) -> None:
        self.active_skills: frozenset[str] = frozenset()
        self.rebuilds = 0

    def activate_skill(self, slug: str) -> None:
        self.active_skills = self.active_skills | {slug}
        self.rebuilds += 1

    def deactivate_skill(self, slug: str) -> bool:
        if slug not in self.active_skills:
            return False
        self.active_skills = self.active_skills - {slug}
        self.rebuilds += 1
        return True


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the command dispatcher at a temporary skills directory."""
    shared = tmp_path / "skills"
    shared.mkdir(parents=True)
    manager = SkillManager(shared_dir=shared, packaged={})
    monkeypatch.setattr(slash_commands, "skill_manager", lambda: manager)
    return shared


def _write(shared: Path, slug: str, body: str, description: str = "does a thing") -> None:
    d = shared / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: {description}\n---\n\n{body}", encoding="utf-8"
    )


async def test_a_skill_command_activates_it(skills: Path, session: FakeSession) -> None:
    _write(skills, "deploy", "Always run the tests before deploying.")

    result = await execute_command("deploy", "", session)  # type: ignore[arg-type]

    assert session.active_skills == {"deploy"}
    assert "deploy" in result.output
    assert "/unload" in result.output, "tell the user how to undo it"
    assert result.prompt == "", "activating alone should not start a turn"


async def test_an_argument_becomes_the_turn(skills: Path, session: FakeSession) -> None:
    """The body is in the system prompt, so only the request needs sending."""
    _write(skills, "review", "Review the diff.")

    result = await execute_command("review", "focus on error handling", session)  # type: ignore[arg-type]

    assert session.active_skills == {"review"}
    assert result.prompt == "focus on error handling"
    assert "Review the diff." not in result.prompt, "the body belongs in the system prompt"


async def test_a_skill_can_be_unloaded_again(skills: Path, session: FakeSession) -> None:
    """The whole point: the reference implementations cannot do this."""
    _write(skills, "flow", "Write a workflow.")
    await execute_command("flow", "", session)  # type: ignore[arg-type]

    result = await execute_command("unload", "flow", session)  # type: ignore[arg-type]

    assert session.active_skills == frozenset()
    assert "Unloaded" in result.output


async def test_unloading_something_inactive_says_so(skills: Path, session: FakeSession) -> None:
    result = await execute_command("unload", "nope", session)  # type: ignore[arg-type]

    assert "not active" in result.output
    assert session.rebuilds == 0, "no need to rebuild the prompt for a no-op"


async def test_unload_with_no_argument_lists_what_is_active(
    skills: Path, session: FakeSession
) -> None:
    _write(skills, "a", "x")
    _write(skills, "b", "y")
    await execute_command("a", "", session)  # type: ignore[arg-type]
    await execute_command("b", "", session)  # type: ignore[arg-type]

    result = await execute_command("unload", "", session)  # type: ignore[arg-type]

    assert "a, b" in result.output
    assert session.active_skills == {"a", "b"}, "listing must not deactivate anything"


async def test_unload_all_drops_everything(skills: Path, session: FakeSession) -> None:
    _write(skills, "a", "x")
    _write(skills, "b", "y")
    await execute_command("a", "", session)  # type: ignore[arg-type]
    await execute_command("b", "", session)  # type: ignore[arg-type]

    result = await execute_command("unload", "all", session)  # type: ignore[arg-type]

    assert session.active_skills == frozenset()
    assert "2" in result.output


async def test_activating_twice_is_idempotent(skills: Path, session: FakeSession) -> None:
    _write(skills, "flow", "x")
    await execute_command("flow", "", session)  # type: ignore[arg-type]
    await execute_command("flow", "", session)  # type: ignore[arg-type]

    assert session.active_skills == {"flow"}


async def test_skills_listing_marks_the_active_ones(skills: Path, session: FakeSession) -> None:
    _write(skills, "on", "x")
    _write(skills, "off", "y")
    await execute_command("on", "", session)  # type: ignore[arg-type]

    result = await execute_command("skills", "", session)  # type: ignore[arg-type]

    active_line = next(ln for ln in result.output.splitlines() if "/on" in ln)
    inactive_line = next(ln for ln in result.output.splitlines() if "/off" in ln)
    assert "[active]" in active_line
    assert "[active]" not in inactive_line


async def test_an_unknown_command_is_still_unknown(skills: Path, session: FakeSession) -> None:
    result = await execute_command("nope", "", session)  # type: ignore[arg-type]

    assert "Unknown command" in result.output
    assert result.prompt == ""
    assert session.active_skills == frozenset()


async def test_a_skill_cannot_shadow_a_builtin(skills: Path, session: FakeSession) -> None:
    """A skills directory is user-writable; `/quit` must keep quitting."""
    _write(skills, "quit", "Do something else entirely.")

    result = await execute_command("quit", "", session)  # type: ignore[arg-type]

    assert result.exit_requested is True
    assert session.active_skills == frozenset()
    assert list_commands()["quit"] == BUILTIN_COMMANDS["quit"]


async def test_skills_are_listed_as_commands_for_completion(skills: Path) -> None:
    _write(skills, "flow", "...", description="write a flow workflow")

    commands = list_commands()

    assert commands["flow"] == "write a flow workflow"
    assert "model" in commands, "built-ins are still there"
    assert "unload" in commands


async def test_skills_command_reports_an_empty_directory_usefully(
    skills: Path, session: FakeSession
) -> None:
    result = await execute_command("skills", "", session)  # type: ignore[arg-type]

    assert "No skills found" in result.output
    assert "pip install xdog-flow" in result.output, "tell the user how to get one"


async def test_skills_command_marks_which_skills_came_from_a_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: FakeSession
) -> None:
    shared = tmp_path / "skills"
    shared.mkdir(parents=True)
    _write(shared, "mine", "local", description="a local one")

    packaged_dir = tmp_path / "pkg"
    packaged_dir.mkdir()
    (packaged_dir / "SKILL.md").write_text(
        "---\nname: flow\ndescription: from a package\n---\n\nbody", encoding="utf-8"
    )
    manager = SkillManager(shared_dir=shared, packaged={"flow": packaged_dir})
    monkeypatch.setattr(slash_commands, "skill_manager", lambda: manager)

    result = await execute_command("skills", "", session)  # type: ignore[arg-type]

    assert "* /flow" in result.output
    assert "  /mine" in result.output
    assert "shipped with an installed package" in result.output


async def test_help_lists_skills_separately_from_builtins(skills: Path) -> None:
    _write(skills, "flow", "...", description="write a flow workflow")

    result = await execute_command("help", "", session=None)  # type: ignore[arg-type]

    assert "Skills:" in result.output
    assert "/flow" in result.output


async def test_a_broken_skills_directory_does_not_break_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch, session: FakeSession
) -> None:
    """Slash commands must keep working when skills cannot be read."""
    def _boom() -> Any:
        raise OSError("permission denied")

    monkeypatch.setattr(slash_commands, "skill_manager", _boom)

    assert "model" in list_commands()
    result = await execute_command("nope", "", session)  # type: ignore[arg-type]
    assert "Unknown command" in result.output


def test_parse_slash_command_splits_name_from_arguments() -> None:
    assert parse_slash_command("/flow add a retry step") == ("flow", "add a retry step")
    assert parse_slash_command("/flow") == ("flow", "")
    assert parse_slash_command("not a command") is None


# -- The system-prompt side: what activation actually changes --


def test_the_system_prompt_gains_and_loses_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deactivation has to leave no trace, or `/unload` is theatre."""
    from xdog.coding.core.agent_session import _skills_context

    shared = tmp_path / "skills"
    shared.mkdir(parents=True)
    _write(shared, "flow", "STEP ONE: validate the graph.", description="write a workflow")
    manager = SkillManager(shared_dir=shared, packaged={})
    monkeypatch.setattr(slash_commands, "skill_manager", lambda: manager)

    idle = _skills_context(frozenset())
    assert "write a workflow" in idle, "the one-line description is always advertised"
    assert "STEP ONE" not in idle, "the body must wait to be asked for"

    active = _skills_context(frozenset({"flow"}))
    assert "STEP ONE: validate the graph." in active

    # ...and back again, byte for byte.
    assert _skills_context(frozenset()) == idle


def test_the_skills_section_is_empty_when_nothing_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xdog.coding.core.agent_session import _skills_context

    manager = SkillManager(shared_dir=tmp_path / "empty", packaged={})
    monkeypatch.setattr(slash_commands, "skill_manager", lambda: manager)

    assert _skills_context(frozenset()) == ""


def test_an_unreadable_skills_directory_does_not_break_the_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xdog.coding.core.agent_session import _skills_context

    def _boom() -> Any:
        raise OSError("permission denied")

    monkeypatch.setattr(slash_commands, "skill_manager", _boom)
    assert _skills_context(frozenset({"flow"})) == ""
