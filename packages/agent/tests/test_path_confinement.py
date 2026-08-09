"""Path confinement — the allowlist mode of `validate_path`.

Two modes with opposite failure behaviour, which is the whole point. The
denylist names a few bad places and allows anything unlisted; the allowlist
names the one good place and *denies* anything unlisted. Getting that backwards
produces a check that passes everything and looks like it works.
"""
from __future__ import annotations

from pathlib import Path

from xdog.agent.tools._utils import validate_path


def test_without_a_bound_an_ordinary_path_is_allowed(tmp_path: Path) -> None:
    """The historical behaviour, which xdog-coding and xdog-claw rely on."""
    assert validate_path(str(tmp_path / "notes.txt")) is None


def test_inside_the_bound_is_allowed(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    workspace.mkdir()

    assert validate_path(str(workspace / "out.json"), confine_to=[workspace]) is None
    assert validate_path(str(workspace / "deep" / "nested.txt"), confine_to=[workspace]) is None
    assert validate_path(str(workspace), confine_to=[workspace]) is None, "the root itself"


def test_outside_the_bound_is_denied(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    workspace.mkdir()
    sibling = tmp_path / "secrets"
    sibling.mkdir()

    error = validate_path(str(sibling / "keys.txt"), confine_to=[workspace])

    assert error is not None
    assert "outside this run's workspace" in error
    assert str(workspace) in error, "say what *is* allowed, not just what is not"


def test_an_unlisted_path_is_denied_rather_than_allowed(tmp_path: Path) -> None:
    """The inversion. A denylist would let `/home/someone/anything` through
    because it is not one of the few named bad places."""
    workspace = tmp_path / "runtime"
    workspace.mkdir()

    assert validate_path("/home/someone/anything", confine_to=[workspace]) is not None
    assert validate_path("/home/someone/anything") is None, "unbounded still allows it"


def test_a_symlink_out_of_the_workspace_is_caught_by_where_it_lands(tmp_path: Path) -> None:
    """Comparing spelled paths would let this through; comparing resolved ones
    does not. A workspace an agent can escape by making one symlink is not a
    workspace."""
    workspace = tmp_path / "runtime"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target.txt").write_text("secret", encoding="utf-8")
    (workspace / "escape").symlink_to(outside)

    error = validate_path(str(workspace / "escape" / "target.txt"), confine_to=[workspace])

    assert error is not None, "a symlink walked out of the workspace"


def test_several_roots_are_allowed_together(tmp_path: Path) -> None:
    """--allow-read adds a tree; the workspace plus the grants are the bound."""
    workspace = tmp_path / "runtime"
    granted = tmp_path / "data"
    other = tmp_path / "elsewhere"
    for d in (workspace, granted, other):
        d.mkdir()

    roots = [workspace, granted]
    assert validate_path(str(workspace / "a"), confine_to=roots) is None
    assert validate_path(str(granted / "b"), confine_to=roots) is None
    assert validate_path(str(other / "c"), confine_to=roots) is not None


def test_an_empty_allowlist_denies_everything(tmp_path: Path) -> None:
    """Distinct from `None`, which means unbounded.

    An empty list is falsy, so the natural `if not roots` reads it as "no bound
    given" and opens the door — which is exactly backwards. A bug upstream that
    computes no roots must close the door, not grant everything.
    """
    assert validate_path(str(tmp_path / "x"), confine_to=[]) is not None


def test_the_tool_helper_also_treats_absent_and_empty_differently() -> None:
    """The same distinction one layer up, where it is easy to lose."""
    from xdog.agent.tools.tool_filesystem import CONFINE_CTX_KEY, _confinement

    assert _confinement({}) is None, "absent means unbounded"
    assert _confinement({CONFINE_CTX_KEY: []}) == [], "empty means deny everything"


def test_the_denylist_still_applies_inside_a_workspace(tmp_path: Path) -> None:
    """Confinement narrows; it does not re-permit."""
    assert validate_path("/etc/passwd", confine_to=[Path("/etc")]) is not None
