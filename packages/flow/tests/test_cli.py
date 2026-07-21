"""Tests for flow.cli — validate, graph, run --dry-run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flow.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"
_LINEAR = str(_FIXTURES / "linear.json")
_BAD = str(_FIXTURES / "bad_missing_entry.json")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_ok(capsys: pytest.CaptureFixture[str]) -> None:
    main(["validate", _LINEAR])
    out = capsys.readouterr().out
    assert "OK:" in out
    assert "linear-workflow" in out


def test_validate_bad_exits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["validate", _BAD])
    assert exc_info.value.code == 1


def test_validate_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["validate", "/nonexistent/path/workflow.json"])
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


def test_graph_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    main(["graph", _LINEAR])
    out = capsys.readouterr().out
    assert "workflow:" in out
    assert "linear-workflow" in out
    assert "nodes:" in out


def test_graph_mermaid(capsys: pytest.CaptureFixture[str]) -> None:
    main(["graph", _LINEAR, "--mermaid"])
    out = capsys.readouterr().out
    assert "flowchart TD" in out
    assert "-->" in out


# ---------------------------------------------------------------------------
# run --dry-run
# ---------------------------------------------------------------------------


async def test_run_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    """dry-run should complete without hitting any LLM and print JSON state."""

    from flow.cli import _cmd_run

    await _cmd_run(_LINEAR, provider=None, dry_run=True)
    out = capsys.readouterr().out
    # Output should be valid JSON
    data = json.loads(out)
    assert isinstance(data, dict)


def test_run_dry_run_sync(capsys: pytest.CaptureFixture[str]) -> None:
    """main() --dry-run integration path."""
    main(["run", _LINEAR, "--dry-run"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_generate_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    main(["generate", _LINEAR])
    out = capsys.readouterr().out
    assert "async def main" in out
    assert "linear-workflow" in out


def test_generate_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_file = tmp_path / "workflow.py"
    main(["generate", _LINEAR, "-o", str(out_file)])
    assert out_file.exists()
    content = out_file.read_text()
    assert "async def main" in content


# ---------------------------------------------------------------------------
# --input K=V (override $in seed at run time)
# ---------------------------------------------------------------------------

_AGENT_CALC = str(Path(__file__).parent.parent / "examples" / "agent_calculator.json")


def test_parse_inputs_basic() -> None:
    from flow.cli import _parse_inputs

    assert _parse_inputs(["a=3", "b=4"]) == {"a": "3", "b": "4"}


def test_parse_inputs_value_with_equals() -> None:
    from flow.cli import _parse_inputs

    # value may contain '=' — split on the first only
    assert _parse_inputs(["note=x=y"]) == {"note": "x=y"}


def test_parse_inputs_missing_equals_errors() -> None:
    from flow.cli import _parse_inputs

    with pytest.raises(SystemExit):
        _parse_inputs(["abc"])


def test_run_input_overrides_seed(capsys: pytest.CaptureFixture[str]) -> None:
    """`run --input a=2 --input b=40 --dry-run` overrides the JSON state defaults."""
    main(["run", _AGENT_CALC, "--input", "a=2", "--input", "b=40", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert out["$in"] == {"a": "2", "b": "40"}
    assert out["make_problem"]["problem"] == "2 + 40"


def test_run_without_input_uses_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    main(["run", _AGENT_CALC, "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert out["make_problem"]["problem"] == "347 + 895"
