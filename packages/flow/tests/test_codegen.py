"""Tests for flow.codegen — linear workflow code generation."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from flow.codegen import generate
from flow.models import EdgeDef, NodeDef, WorkflowDef


def _make_linear_wf() -> WorkflowDef:
    return WorkflowDef(
        name="test_workflow",
        provider="anthropic",
        entry="step1",
        nodes=(
            NodeDef(
                id="step1",
                model="claude-3-haiku",
                system_prompt="You are a helper.",
                prompt="Say hello",
                output="greeting",
            ),
            NodeDef(
                id="step2",
                model="claude-3-haiku",
                system_prompt="Summarize.",
                prompt="Summarize: {{greeting}}",
                output="summary",
            ),
        ),
        edges=(EdgeDef(src="step1", dst="step2"),),
        default_model="claude-3-haiku",
        initial_state=(("topic", "testing"),),
    )


def test_generate_linear_contains_node_functions() -> None:
    wf = _make_linear_wf()
    src = generate(wf)
    assert "async def node_step1(provider" in src
    assert "async def node_step2(provider" in src


def test_generate_linear_contains_main_guard() -> None:
    wf = _make_linear_wf()
    src = generate(wf)
    assert '__name__ == "__main__"' in src or "__name__ == '__main__'" in src


def test_generate_linear_compiles() -> None:
    wf = _make_linear_wf()
    src = generate(wf)
    compile(src, "<generated>", "exec")


def test_generate_linear_ruff_clean() -> None:
    wf = _make_linear_wf()
    src = generate(wf)
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(src)
        tmp = Path(f.name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--line-length", "120", str(tmp)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ruff failed:\n{result.stdout}\n{result.stderr}"
    finally:
        tmp.unlink(missing_ok=True)


def test_generate_linear_state_seed() -> None:
    wf = _make_linear_wf()
    src = generate(wf)
    assert '"topic"' in src
    assert '"testing"' in src


def test_generate_linear_provider_name() -> None:
    wf = _make_linear_wf()
    src = generate(wf)
    assert 'ai.provider("anthropic")' in src
