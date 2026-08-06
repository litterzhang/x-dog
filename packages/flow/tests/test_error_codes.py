"""Error codes are API — pin the properties that make them worth having.

The point of a code is that a caller can branch on it without pattern-matching
English. That only holds if every failure carries one, and if the set stays
small enough to actually branch on. Both are easy to erode one commit at a
time: a new check gets added without a code, or a code gets minted for a single
site because none of the existing ones felt exact.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from xdog.flow import error_codes as codes
from xdog.flow.cli import main as cli_main
from xdog.flow.errors import WorkflowValidationError

_LOADER = pathlib.Path(__file__).resolve().parents[1] / "src" / "xdog" / "flow" / "loader.py"


def _construction_sites() -> list[str]:
    """Every `WorkflowValidationError(...)` in the loader, with balanced parens."""
    src = _LOADER.read_text(encoding="utf-8")
    sites: list[str] = []
    i = 0
    while (i := src.find("WorkflowValidationError(", i)) != -1:
        j = i + len("WorkflowValidationError(")
        depth = 1
        while depth and j < len(src):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
            j += 1
        sites.append(src[i:j])
        i = j
    return sites


def test_every_validation_failure_carries_a_code() -> None:
    """One uncoded check is enough to force a caller back to string matching."""
    sites = _construction_sites()
    assert len(sites) > 50, "the scraper stopped finding call sites — fix it, don't delete this"

    uncoded = [s for s in sites if "code=" not in s]
    assert not uncoded, f"{len(uncoded)} validation errors have no code, e.g. {uncoded[0][:160]}"


def test_no_code_is_used_that_is_not_declared() -> None:
    used = set(re.findall(r"code=codes\.([A-Z_]+)", _LOADER.read_text(encoding="utf-8")))
    declared = {n for n in dir(codes) if n.isupper() and n != "ALL_CODES"}

    assert used - declared == set(), f"undeclared codes in use: {sorted(used - declared)}"


def test_no_code_is_declared_without_being_used() -> None:
    """A code nobody raises is a promise to consumers that nothing keeps."""
    used = set(re.findall(r"code=codes\.([A-Z_]+)", _LOADER.read_text(encoding="utf-8")))
    declared = {n for n in dir(codes) if n.isupper() and n != "ALL_CODES"}

    assert declared - used == set(), f"declared but never raised: {sorted(declared - used)}"


def test_the_code_set_stays_small_enough_to_branch_on() -> None:
    """Ninety-nine checks, eighteen codes. If this ever approaches the number of
    check sites, the codes have stopped being a taxonomy and become identifiers,
    and a caller cannot write a handler for each."""
    assert len(codes.ALL_CODES) <= 25, f"{len(codes.ALL_CODES)} codes is too many to handle"
    assert len(set(codes.ALL_CODES)) == len(codes.ALL_CODES), "duplicate code strings"


def test_codes_are_lowercase_hyphenated() -> None:
    """Stable across languages and safe in a URL, a log line or a shell script.

    One test, not one per code: they would all fail together for the same
    reason, and eighteen red lines say nothing that one listing them does not.
    """
    bad = [c for c in codes.ALL_CODES if not re.fullmatch(r"[a-z]+(-[a-z]+)*", c)]
    assert not bad, f"not kebab-case slugs: {bad}"


def test_as_dict_omits_absent_fields_rather_than_nulling_them() -> None:
    bare = WorkflowValidationError("something broke").as_dict()
    assert bare == {"message": "something broke"}

    full = WorkflowValidationError(
        "bad edge", code=codes.TYPE_MISMATCH, node="b", edge=("a", "b"), hint="cast it"
    ).as_dict()
    assert full == {
        "message": "bad edge",
        "code": "type-mismatch",
        "node": "b",
        "edge": {"from": "a", "to": "b"},
        "hint": "cast it",
    }


def test_validate_json_reports_a_code_for_every_error(tmp_path, capsys) -> None:
    """End to end, which is where a consumer actually meets this."""
    import json

    broken = tmp_path / "broken.json"
    broken.write_text(
        json.dumps(
            {
                "name": "broken",
                "entry": "a",
                "nodes": [
                    {
                        "id": "a",
                        "type": "script",
                        "inputs": ["x"],
                        "code": "def a(ctx, x):\n    return x",
                        "outputs": ["y"],
                    }
                ],
                "edges": [{"from": "a", "to": "nowhere", "map": {"y": "y"}}],
            }
        ),
        encoding="utf-8",
    )

    # A non-zero exit is the point of `validate` — the report still goes to stdout.
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["validate", str(broken), "--json"])
    assert exit_info.value.code == 1

    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["errors"], "expected the broken workflow to produce errors"
    for error in report["errors"]:
        assert error.get("code") in codes.ALL_CODES, f"missing or unknown code: {error}"


def test_the_readme_table_lists_every_code() -> None:
    """A code a consumer cannot look up is barely better than no code.

    The table drifts the moment someone adds a code and not a row, and nothing
    else would notice — the docs still render, the tests still pass.
    """
    readme = (_LOADER.parents[3] / "README.md").read_text(encoding="utf-8")

    # Scope to the error-code table: the README has other tables whose first
    # column is also a backticked lowercase word (the condition operators), and
    # matching those would make this test fail for an unrelated edit.
    section = readme.split("#### Error codes", 1)
    assert len(section) == 2, "the README no longer has an '#### Error codes' section"
    table = section[1].split("\n\n##", 1)[0]
    documented = set(re.findall(r"^\| `([a-z-]+)` \|", table, re.MULTILINE))

    missing = set(codes.ALL_CODES) - documented
    assert not missing, f"codes with no row in the README table: {sorted(missing)}"

    stale = documented - set(codes.ALL_CODES)
    assert not stale, f"README documents codes that no longer exist: {sorted(stale)}"
