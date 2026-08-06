"""Tests for skills exposed as slash commands.

The wiring that matters is not that `/flow` prints something — it is that the
skill's text reaches the model. A command that renders SKILL.md into the
terminal looks like it works and teaches the agent nothing.
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


async def test_a_skill_becomes_a_command_that_prompts_the_model(skills: Path) -> None:
    _write(skills, "deploy", "Always run the tests before deploying.")

    result = await execute_command("deploy", "", session=None)  # type: ignore[arg-type]

    assert "Always run the tests" in result.prompt, "the skill body must reach the model"
    assert "deploy" in result.output, "and the user should see which skill ran"
    assert not result.exit_requested


async def test_arguments_are_appended_to_the_skill_body(skills: Path) -> None:
    _write(skills, "review", "Review the diff.")

    result = await execute_command("review", "focus on error handling", session=None)  # type: ignore[arg-type]

    assert result.prompt.startswith("Review the diff.")
    assert result.prompt.endswith("focus on error handling")


async def test_an_unknown_command_is_still_unknown(skills: Path) -> None:
    result = await execute_command("nope", "", session=None)  # type: ignore[arg-type]

    assert "Unknown command" in result.output
    assert result.prompt == ""


async def test_a_skill_cannot_shadow_a_builtin(skills: Path) -> None:
    """A skills directory is user-writable; `/quit` must keep quitting."""
    _write(skills, "quit", "Do something else entirely.")

    result = await execute_command("quit", "", session=None)  # type: ignore[arg-type]

    assert result.exit_requested is True
    assert result.prompt == ""
    assert list_commands()["quit"] == BUILTIN_COMMANDS["quit"]


async def test_skills_are_listed_as_commands_for_completion(skills: Path) -> None:
    _write(skills, "flow", "...", description="write a flow workflow")

    commands = list_commands()

    assert commands["flow"] == "write a flow workflow"
    assert "model" in commands, "built-ins are still there"


async def test_skills_command_reports_an_empty_directory_usefully(skills: Path) -> None:
    result = await execute_command("skills", "", session=None)  # type: ignore[arg-type]

    assert "No skills found" in result.output
    assert "pip install xdog-flow" in result.output, "tell the user how to get one"


async def test_skills_command_marks_which_skills_came_from_a_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    result = await execute_command("skills", "", session=None)  # type: ignore[arg-type]

    assert "* /flow" in result.output
    assert "  /mine" in result.output
    assert "shipped with an installed package" in result.output


async def test_help_lists_skills_separately_from_builtins(skills: Path) -> None:
    _write(skills, "flow", "...", description="write a flow workflow")

    result = await execute_command("help", "", session=None)  # type: ignore[arg-type]

    assert "Skills:" in result.output
    assert "/flow" in result.output


async def test_a_broken_skills_directory_does_not_break_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slash commands must keep working when skills cannot be read."""
    def _boom() -> Any:
        raise OSError("permission denied")

    monkeypatch.setattr(slash_commands, "skill_manager", _boom)

    assert "model" in list_commands()
    result = await execute_command("nope", "", session=None)  # type: ignore[arg-type]
    assert "Unknown command" in result.output


def test_parse_slash_command_splits_name_from_arguments() -> None:
    assert parse_slash_command("/flow add a retry step") == ("flow", "add a retry step")
    assert parse_slash_command("/flow") == ("flow", "")
    assert parse_slash_command("not a command") is None
