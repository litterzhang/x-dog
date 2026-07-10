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


def _make_parallel_wf() -> WorkflowDef:
    """Diamond: start -> (left, right) -> end."""
    return WorkflowDef(
        name="parallel_workflow",
        provider="anthropic",
        entry="start",
        nodes=(
            NodeDef(id="start", model="claude-3-haiku", system_prompt="S", prompt="P", output="s_out"),
            NodeDef(id="left", model="claude-3-haiku", system_prompt="L", prompt="L", output="l_out"),
            NodeDef(id="right", model="claude-3-haiku", system_prompt="R", prompt="R", output="r_out"),
            NodeDef(id="end", model="claude-3-haiku", system_prompt="E", prompt="E", output="e_out"),
        ),
        edges=(
            EdgeDef(src="start", dst="left"),
            EdgeDef(src="start", dst="right"),
            EdgeDef(src="left", dst="end"),
            EdgeDef(src="right", dst="end"),
        ),
        default_model="claude-3-haiku",
    )


def test_generate_parallel() -> None:
    wf = _make_parallel_wf()
    src = generate(wf)
    # asyncio.gather must appear for the parallel wave
    assert "asyncio.gather(" in src
    # both parallel nodes must be present in the gather call
    assert "node_left" in src
    assert "node_right" in src
    # must compile cleanly
    compile(src, "<generated>", "exec")
    # must pass ruff
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


def _make_loop_wf() -> WorkflowDef:
    """Linear loop: draft -> review, with a loop back-edge review -> draft (max 3)."""
    return WorkflowDef(
        name="loop_workflow",
        provider="anthropic",
        entry="draft",
        nodes=(
            NodeDef(id="draft", model="claude-3-haiku", system_prompt="D", prompt="Write draft", output="draft_out"),
            NodeDef(id="review", model="claude-3-haiku", system_prompt="R", prompt="Review", output="verdict"),
            NodeDef(id="publish", model="claude-3-haiku", system_prompt="P", prompt="Publish", output="pub_out"),
        ),
        edges=(
            EdgeDef(src="draft", dst="review"),
            EdgeDef(src="review", dst="draft", loop_max=3),
            EdgeDef(src="review", dst="publish"),
        ),
        default_model="claude-3-haiku",
    )


def test_generate_loop() -> None:
    wf = _make_loop_wf()
    src = generate(wf)
    # A bounded loop must appear as range(loop_max)
    assert "range(3)" in src
    # must compile cleanly
    compile(src, "<generated>", "exec")
    # must pass ruff
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
