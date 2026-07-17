"""Tests for flow.codegen — linear workflow code generation."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from flow.codegen import generate
from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef


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


def test_generate_with_tools() -> None:
    wf = WorkflowDef(
        name="tools_workflow",
        provider="anthropic",
        entry="step1",
        nodes=(
            NodeDef(
                id="step1",
                model="claude-3-haiku",
                system_prompt="You are a helper.",
                prompt="Use echo.",
                output="result",
                tools=("echo",),
            ),
        ),
        edges=(),
        default_model="claude-3-haiku",
    )
    src = generate(wf)
    assert "_REGISTRY.resolve(" in src
    assert '"echo"' in src
    compile(src, "<generated>", "exec")
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


def test_generate_script() -> None:
    wf = WorkflowDef(
        name="script_workflow",
        provider="anthropic",
        entry="step1",
        nodes=(
            NodeDef(
                id="step1",
                type="script",
                run="myscripts:prep",
                output="result",
            ),
        ),
        edges=(),
        default_model="claude-3-haiku",
    )
    src = generate(wf)
    assert "prep as _script_" in src
    assert "await _script_" in src  # run-ref functions are awaited
    compile(src, "<generated>", "exec")
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


# ---------------------------------------------------------------------------
# Regression: conditional branches and conditional loops must generate code
# whose runtime behaviour matches the interpreter (execute()).  These are pure
# script workflows, so the generated module runs with no LLM.
# ---------------------------------------------------------------------------


async def _run_generated(wf: WorkflowDef) -> dict[str, str]:
    """Generate wf, ruff-check it, exec the module in-process, return final STATE."""
    src = generate(wf)
    compile(src, "<generated>", "exec")
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(src)
        tmp = Path(f.name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--line-length", "120", str(tmp)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"generated code not ruff-clean:\n{src}\n{result.stdout}"
        spec = importlib.util.spec_from_file_location(f"gen_{uuid.uuid4().hex}", tmp)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # The test already runs inside an event loop (pytest-asyncio), so await
        # the generated coroutine directly rather than asyncio.run().
        await mod.main()
        return dict(mod.STATE)
    finally:
        tmp.unlink(missing_ok=True)


async def test_generate_conditional_branch_matches_runtime() -> None:
    """route -> (odd | even) guarded edges: only the matching branch runs.

    Regression for the bug where two conditionally-reached nodes in the same BFS
    wave were emitted as an unconditional ``asyncio.gather`` (both branches ran,
    dropping the ``when`` guards).
    """
    from flow.executor import execute

    wf = WorkflowDef(
        name="cond-branch",
        provider="copilot",
        entry="route",
        default_model="m",
        initial_state=(("n", "7"),),
        nodes=(
            NodeDef(
                id="route",
                type="script",
                inputs=("n",),
                input_schema=(("n", "integer"),),
                code="def route(ctx, n):\n    return 'odd' if n % 2 else 'even'",
                output="kind",
                output_type="string",
            ),
            NodeDef(
                id="handle_odd",
                type="script",
                inputs=("n",),
                input_schema=(("n", "integer"),),
                code="def handle_odd(ctx, n):\n    return f'ODD:{n * 3}'",
                output="result",
                output_type="string",
            ),
            NodeDef(
                id="handle_even",
                type="script",
                inputs=("n",),
                input_schema=(("n", "integer"),),
                code="def handle_even(ctx, n):\n    return f'EVEN:{n // 2}'",
                output="result",
                output_type="string",
            ),
        ),
        edges=(
            EdgeDef(src="route", dst="handle_odd", when=Condition(op="equals", value="{{kind}}", text="odd")),
            EdgeDef(src="route", dst="handle_even", when=Condition(op="equals", value="{{kind}}", text="even")),
        ),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    # n=7 is odd -> only handle_odd fires
    assert gen_state["result"] == "ODD:21"
    # generated code agrees with the interpreter
    assert gen_state["result"] == run_result.final_state["result"]


async def test_generate_conditional_loop_matches_runtime() -> None:
    """A conditional back-edge loop must early-exit when its guard fails.

    Regression for the bug where a loop was emitted as ``for _ in range(max)``
    with no break, so it always ran the maximum iterations instead of stopping
    when the back-edge condition stopped holding.
    """
    from flow.executor import execute

    wf = WorkflowDef(
        name="cond-loop",
        provider="copilot",
        entry="init",
        default_model="m",
        initial_state=(("counter", "0"),),
        nodes=(
            NodeDef(
                id="init",
                type="script",
                inputs=("counter",),
                input_schema=(("counter", "integer"),),
                code="def init(ctx, counter):\n    return counter",
                output="c",
                output_type="integer",
            ),
            NodeDef(
                id="inc",
                type="script",
                inputs=("c",),
                input_schema=(("c", "integer"),),
                code="def inc(ctx, c):\n    return c + 1",
                output="c",
                output_type="integer",
            ),
        ),
        edges=(
            EdgeDef(src="init", dst="inc"),
            EdgeDef(
                src="inc",
                dst="inc",
                when=Condition(op="not", children=(Condition(op="equals", value="{{c}}", text="3"),)),
                loop_max=10,
            ),
        ),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    # increments stop as soon as c == 3, not after all 10 iterations
    assert gen_state["c"] == "3"
    assert gen_state["c"] == run_result.final_state["c"]
