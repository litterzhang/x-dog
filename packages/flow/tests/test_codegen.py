"""Tests for flow.codegen — port-model workflow code generation.

Generated modules keep node-private outputs in a nested ``_OUT[node][port]`` dict
(``$in`` + real nodes).  The regression tests assert the generated module and the
interpreter agree by comparing that dict to the interpreter's reconstructed
equivalent (``{$in: runtime["in"], **runtime["state"]}``).
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from flow.codegen import generate
from flow.executor import ExecResult
from flow.models import IN_NODE_ID, Condition, EdgeDef, NodeDef, Port, RetryPolicy, WorkflowDef


def _interp_out(result: ExecResult) -> dict[str, dict[str, str]]:
    """Reconstruct the generated ``_OUT`` shape ($in + real nodes) from a runtime."""
    return {IN_NODE_ID: dict(result.runtime["in"]), **{k: dict(v) for k, v in result.runtime["state"].items()}}


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
                output_ports=(Port("greeting"),),
            ),
            NodeDef(
                id="step2",
                model="claude-3-haiku",
                system_prompt="Summarize.",
                prompt="Summarize: {{greeting}}",
                input_ports=(Port("greeting"),),
                output_ports=(Port("summary"),),
            ),
        ),
        edges=(EdgeDef(src="step1", dst="step2", mapping=(("greeting", "greeting"),)),),
        default_model="claude-3-haiku",
        initial_state=(("topic", "testing"),),
    )


def _ruff_clean(src: str) -> tuple[bool, str]:
    """Compile *src* and run ruff; return (ok, message)."""
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
        return result.returncode == 0, result.stdout + result.stderr
    finally:
        tmp.unlink(missing_ok=True)


def test_generate_linear_contains_node_functions() -> None:
    src = generate(_make_linear_wf())
    assert "async def node_step1(provider" in src
    assert "async def node_step2(provider" in src


def test_generate_linear_contains_main_guard() -> None:
    src = generate(_make_linear_wf())
    assert '__name__ == "__main__"' in src or "__name__ == '__main__'" in src


def test_generate_linear_compiles() -> None:
    compile(generate(_make_linear_wf()), "<generated>", "exec")


def test_generate_linear_ruff_clean() -> None:
    ok, msg = _ruff_clean(generate(_make_linear_wf()))
    assert ok, f"ruff failed:\n{msg}"


def test_generate_linear_state_seed() -> None:
    src = generate(_make_linear_wf())
    # initial_state is emitted via repr() (proper escaping), so keys/values are
    # single-quoted Python literals.
    assert "'topic'" in src
    assert "'testing'" in src


def test_generate_linear_provider_name() -> None:
    # The provider literal is the FLOW_PROVIDER default (override-aware).
    assert 'ai.provider(os.environ.get("FLOW_PROVIDER") or "anthropic")' in generate(_make_linear_wf())


def _make_parallel_wf() -> WorkflowDef:
    """Diamond: start -> (left, right) -> end."""
    return WorkflowDef(
        name="parallel_workflow",
        provider="anthropic",
        entry="start",
        nodes=(
            NodeDef(id="start", model="claude-3-haiku", system_prompt="S", prompt="P", output_ports=(Port("s_out"),)),
            NodeDef(
                id="left",
                model="claude-3-haiku",
                system_prompt="L",
                prompt="L {{s_out}}",
                input_ports=(Port("s_out"),),
                output_ports=(Port("l_out"),),
            ),
            NodeDef(
                id="right",
                model="claude-3-haiku",
                system_prompt="R",
                prompt="R {{s_out}}",
                input_ports=(Port("s_out"),),
                output_ports=(Port("r_out"),),
            ),
            NodeDef(
                id="end",
                model="claude-3-haiku",
                system_prompt="E",
                prompt="E {{l_out}} {{r_out}}",
                input_ports=(Port("l_out"), Port("r_out")),
                output_ports=(Port("e_out"),),
            ),
        ),
        edges=(
            EdgeDef(src="start", dst="left", mapping=(("s_out", "s_out"),)),
            EdgeDef(src="start", dst="right", mapping=(("s_out", "s_out"),)),
            EdgeDef(src="left", dst="end", mapping=(("l_out", "l_out"),)),
            EdgeDef(src="right", dst="end", mapping=(("r_out", "r_out"),)),
        ),
        default_model="claude-3-haiku",
    )


def test_generate_parallel() -> None:
    src = generate(_make_parallel_wf())
    assert "asyncio.gather(" in src
    assert "node_left" in src
    assert "node_right" in src
    ok, msg = _ruff_clean(src)
    assert ok, f"ruff failed:\n{msg}"


def _make_loop_wf() -> WorkflowDef:
    """Linear loop: draft -> review, with a loop back-edge review -> draft (max 3)."""
    return WorkflowDef(
        name="loop_workflow",
        provider="anthropic",
        entry="draft",
        nodes=(
            NodeDef(
                id="draft",
                model="claude-3-haiku",
                system_prompt="D",
                prompt="Write draft",
                output_ports=(Port("draft_out"),),
            ),
            NodeDef(
                id="review",
                model="claude-3-haiku",
                system_prompt="R",
                prompt="Review {{draft_out}}",
                input_ports=(Port("draft_out"),),
                output_ports=(Port("verdict"),),
            ),
            NodeDef(
                id="publish",
                model="claude-3-haiku",
                system_prompt="P",
                prompt="Publish {{verdict}}",
                input_ports=(Port("verdict"),),
                output_ports=(Port("pub_out"),),
            ),
        ),
        edges=(
            EdgeDef(src="draft", dst="review", mapping=(("draft_out", "draft_out"),)),
            EdgeDef(src="review", dst="draft", loop_max=3),
            EdgeDef(src="review", dst="publish", mapping=(("verdict", "verdict"),)),
        ),
        default_model="claude-3-haiku",
    )


def test_generate_loop() -> None:
    src = generate(_make_loop_wf())
    # A bounded loop compiles to a for-range that resumes from its persisted counter
    # (depth-indexed loop var; ticks the counter each iteration).
    assert "range(_loop_start(" in src and ", 3):" in src
    assert "_loop_i_0" in src
    assert "_loop_tick(" in src
    ok, msg = _ruff_clean(src)
    assert ok, f"ruff failed:\n{msg}"


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
                output_ports=(Port("result"),),
                tools=("echo",),
            ),
        ),
        edges=(),
        default_model="claude-3-haiku",
    )
    src = generate(wf)
    assert "_REGISTRY.resolve(" in src
    assert '"echo"' in src
    ok, msg = _ruff_clean(src)
    assert ok, f"ruff failed:\n{msg}"


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
                output_ports=(Port("result"),),
            ),
        ),
        edges=(),
        default_model="claude-3-haiku",
    )
    src = generate(wf)
    assert "prep as _script_" in src
    assert "await _script_" in src  # run-ref functions are awaited
    ok, msg = _ruff_clean(src)
    assert ok, f"ruff failed:\n{msg}"


# ---------------------------------------------------------------------------
# Regression: conditional branches and conditional loops must generate code
# whose runtime behaviour matches the interpreter (execute()).  These are pure
# script workflows, so the generated module runs with no LLM.  Comparisons are
# over the nested ``_OUT`` / ``outputs`` shape.
# ---------------------------------------------------------------------------


async def _run_generated(wf: WorkflowDef) -> dict[str, dict[str, str]]:
    """Generate wf, ruff-check it, exec the module in-process, return nested _OUT."""
    src = generate(wf)
    ok, msg = _ruff_clean(src)
    assert ok, f"generated code not ruff-clean:\n{src}\n{msg}"
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(src)
        tmp = Path(f.name)
    try:
        spec = importlib.util.spec_from_file_location(f"gen_{uuid.uuid4().hex}", tmp)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # The test already runs inside an event loop (pytest-asyncio), so await
        # the generated coroutine directly rather than asyncio.run().
        await mod.main()
        return {k: dict(v) for k, v in mod._OUT.items()}
    finally:
        tmp.unlink(missing_ok=True)


async def test_generate_conditional_branch_matches_runtime() -> None:
    """route -> (odd | even) guarded edges: only the matching branch runs.

    Regression for the bug where two conditionally-reached nodes in the same BFS
    wave were emitted as an unconditional ``asyncio.gather`` (both branches ran).
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
                input_ports=(Port("n", "integer"),),
                code="def route(ctx, n):\n    return 'odd' if n % 2 else 'even'",
                output_ports=(Port("kind", "string"),),
            ),
            NodeDef(
                id="handle_odd",
                type="script",
                input_ports=(Port("n", "integer"),),
                code="def handle_odd(ctx, n):\n    return f'ODD:{n * 3}'",
                output_ports=(Port("result", "string"),),
            ),
            NodeDef(
                id="handle_even",
                type="script",
                input_ports=(Port("n", "integer"),),
                code="def handle_even(ctx, n):\n    return f'EVEN:{n // 2}'",
                output_ports=(Port("result", "string"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="route", mapping=(("n", "n"),)),
            EdgeDef(src=IN_NODE_ID, dst="handle_odd", mapping=(("n", "n"),)),
            EdgeDef(src=IN_NODE_ID, dst="handle_even", mapping=(("n", "n"),)),
            EdgeDef(src="route", dst="handle_odd", when=Condition(op="equals", value="{{kind}}", text="odd")),
            EdgeDef(src="route", dst="handle_even", when=Condition(op="equals", value="{{kind}}", text="even")),
        ),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    # n=7 is odd -> only handle_odd fires
    assert gen_state["handle_odd"]["result"] == "ODD:21"
    assert "handle_even" not in gen_state
    assert gen_state == _interp_out(run_result)


async def test_generate_conditional_loop_matches_runtime() -> None:
    """A conditional back-edge loop must early-exit when its guard fails."""
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
                input_ports=(Port("counter", "integer"),),
                code="def init(ctx, counter):\n    return counter",
                output_ports=(Port("c", "integer"),),
            ),
            NodeDef(
                id="inc",
                type="script",
                input_ports=(Port("c", "integer"),),
                code="def inc(ctx, c):\n    return c + 1",
                output_ports=(Port("c", "integer"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="init", mapping=(("counter", "counter"),)),
            EdgeDef(src="init", dst="inc", mapping=(("c", "c"),)),
            EdgeDef(
                src="inc",
                dst="inc",
                mapping=(("c", "c"),),  # feed inc's own output back to its input each iteration
                when=Condition(op="not", children=(Condition(op="equals", value="{{c}}", text="3"),)),
                loop_max=10,
            ),
        ),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    # increments stop as soon as c == 3, not after all 10 iterations
    assert gen_state["inc"]["c"] == 3
    assert gen_state == _interp_out(run_result)


async def test_generate_colliding_node_ids_stay_distinct() -> None:
    """Node ids that normalise to the same identifier must not shadow each other."""
    from flow.executor import execute

    wf = WorkflowDef(
        name="collide",
        provider="copilot",
        entry="a-b",
        default_model="m",
        initial_state=(("x", "1"),),
        nodes=(
            NodeDef(
                id="a-b",
                type="script",
                code="def f(ctx, x):\n    return x + '-B'",
                output_ports=(Port("vb", "string"),),
                input_ports=(Port("x", "string"),),
            ),
            NodeDef(
                id="a.b",
                type="script",
                code="def g(ctx, vb):\n    return vb + '.B'",
                output_ports=(Port("vc", "string"),),
                input_ports=(Port("vb", "string"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="a-b", mapping=(("x", "x"),)),
            EdgeDef(src="a-b", dst="a.b", mapping=(("vb", "vb"),)),
        ),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    assert gen_state["a.b"]["vc"] == "1-B.B"
    assert gen_state == _interp_out(run_result)


async def test_generate_cross_dependency_orders_correctly() -> None:
    """A->B, A->C, B->C: C depends on A AND B, so C must run after B (not with it).

    A naive "gather all successors of A" would put B and C in one wave and run C
    before B finished.  The generated code must sequence A; B; C, matching the
    interpreter.  x=5 -> a=6, b=a*10=60, c=a+b=66.
    """
    from flow.executor import execute

    def sc(nid: str, code: str, out: str, inp: tuple[Port, ...]) -> NodeDef:
        return NodeDef(id=nid, type="script", code=code, output_ports=(Port(out, "integer"),), input_ports=inp)

    wf = WorkflowDef(
        name="cross-dep",
        provider="copilot",
        entry="A",
        default_model="m",
        initial_state=(("x", "5"),),
        nodes=(
            sc("A", "def A(ctx, x):\n    return x + 1", "a", (Port("x", "integer"),)),
            sc("B", "def B(ctx, a):\n    return a * 10", "b", (Port("a", "integer"),)),
            sc("C", "def C(ctx, a, b):\n    return a + b", "c", (Port("a", "integer"), Port("b", "integer"))),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="A", mapping=(("x", "x"),)),
            EdgeDef(src="A", dst="B", mapping=(("a", "a"),)),
            EdgeDef(src="A", dst="C", mapping=(("a", "a"),)),
            EdgeDef(src="B", dst="C", mapping=(("b", "b"),)),
        ),
    )

    src = generate(wf)
    # B and C must NOT be gathered together (C waits for B).
    assert "gather(node_B" not in src and "gather(node_C" not in src

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    assert gen_state["C"]["c"] == 66
    assert gen_state["B"]["b"] == 60
    assert gen_state == _interp_out(run_result)


async def test_generate_escapes_initial_state_values() -> None:
    """initial_state values with backslashes/quotes/newlines must survive verbatim."""
    from flow.executor import execute

    wf = WorkflowDef(
        name="escape",
        provider="copilot",
        entry="mk",
        default_model="m",
        initial_state=(("p", "a\\b"),),  # a literal backslash between a and b
        nodes=(
            NodeDef(
                id="mk",
                type="script",
                code="def mk(ctx, p):\n    return p + '!' ",
                output_ports=(Port("out", "string"),),
                input_ports=(Port("p", "string"),),
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="mk", mapping=(("p", "p"),)),),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    assert gen_state[IN_NODE_ID]["p"] == "a\\b"
    assert gen_state["mk"]["out"] == "a\\b!"
    assert gen_state == _interp_out(run_result)


def _sc(nid: str, code: str, out: str, inp: tuple[Port, ...] = ()) -> NodeDef:
    return NodeDef(id=nid, type="script", code=code, output_ports=(Port(out, "string"),), input_ports=inp)


async def test_generate_conditional_fan_in_skips_when_a_branch_is_skipped() -> None:
    """A fan-in node waits for ALL predecessors; a skipped branch skips it too."""
    from flow.executor import execute

    wf = WorkflowDef(
        name="cond-fan-in",
        provider="copilot",
        entry="route",
        default_model="m",
        initial_state=(("n", "7"),),
        nodes=(
            _sc("route", "def route(ctx, n):\n    return 'odd' if n % 2 else 'even'", "kind", (Port("n", "integer"),)),
            _sc("odd", "def odd(ctx, n):\n    return f'O{n}'", "branch", (Port("n", "integer"),)),
            _sc("even", "def even(ctx, n):\n    return f'E{n}'", "branch", (Port("n", "integer"),)),
            _sc("merge", "def merge(ctx, branch):\n    return 'got ' + branch", "final", (Port("branch", "string"),)),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="route", mapping=(("n", "n"),)),
            EdgeDef(src=IN_NODE_ID, dst="odd", mapping=(("n", "n"),)),
            EdgeDef(src=IN_NODE_ID, dst="even", mapping=(("n", "n"),)),
            EdgeDef(src="route", dst="odd", when=Condition(op="equals", value="{{kind}}", text="odd")),
            EdgeDef(src="route", dst="even", when=Condition(op="equals", value="{{kind}}", text="even")),
            EdgeDef(src="odd", dst="merge", mapping=(("branch", "branch"),)),
            EdgeDef(src="even", dst="merge", mapping=(("branch", "branch"),)),
        ),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    assert "merge" not in gen_state  # merge skipped: even branch never ran
    assert gen_state["odd"]["branch"] == "O7"
    assert gen_state == _interp_out(run_result)


async def test_generate_conditional_skip_propagates_downstream() -> None:
    """When a guarded node is skipped, its unconditional successor is skipped too."""
    from flow.executor import execute

    wf = WorkflowDef(
        name="cond-skip",
        provider="copilot",
        entry="route",
        default_model="m",
        initial_state=(("n", "7"),),
        nodes=(
            NodeDef(
                id="route",
                type="script",
                code="def route(ctx, n):\n    return 'weird'",
                output_ports=(Port("kind", "string"),),
                input_ports=(Port("n", "integer"),),
            ),
            NodeDef(id="a", type="script", code="def a(ctx):\n    return 'A'", output_ports=(Port("ra", "string"),)),
            NodeDef(
                id="b",
                type="script",
                code="def b(ctx, ra):\n    return ra + 'B'",
                output_ports=(Port("rb", "string"),),
                input_ports=(Port("ra", "string"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="route", mapping=(("n", "n"),)),
            EdgeDef(src="route", dst="a", when=Condition(op="equals", value="{{kind}}", text="never")),
            EdgeDef(src="a", dst="b", mapping=(("ra", "ra"),)),
        ),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    assert "a" not in gen_state and "b" not in gen_state  # a skipped -> b skipped
    assert gen_state == _interp_out(run_result)


async def test_generate_conditional_branch_positive_path_runs_downstream() -> None:
    """The taken branch runs (n=4 -> even)."""
    from flow.executor import execute

    wf = WorkflowDef(
        name="cond-pos",
        provider="copilot",
        entry="route",
        default_model="m",
        initial_state=(("n", "4"),),
        nodes=(
            _sc("route", "def route(ctx, n):\n    return 'odd' if n % 2 else 'even'", "kind", (Port("n", "integer"),)),
            _sc("odd", "def odd(ctx, n):\n    return f'O{n}'", "branch", (Port("n", "integer"),)),
            _sc("even", "def even(ctx, n):\n    return f'E{n}'", "branch", (Port("n", "integer"),)),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="route", mapping=(("n", "n"),)),
            EdgeDef(src=IN_NODE_ID, dst="odd", mapping=(("n", "n"),)),
            EdgeDef(src=IN_NODE_ID, dst="even", mapping=(("n", "n"),)),
            EdgeDef(src="route", dst="odd", when=Condition(op="equals", value="{{kind}}", text="odd")),
            EdgeDef(src="route", dst="even", when=Condition(op="equals", value="{{kind}}", text="even")),
        ),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    assert gen_state["even"]["branch"] == "E4"
    assert gen_state == _interp_out(run_result)


# ---------------------------------------------------------------------------
# custom tool manifest
# ---------------------------------------------------------------------------


def _make_tool_manifest_wf() -> WorkflowDef:
    return WorkflowDef(
        name="tool_manifest",
        provider="copilot",
        entry="build",
        default_model="gpt-4o",
        nodes=(
            NodeDef(
                id="build",
                model="gpt-4o",
                prompt="reverse {{topic}}",
                input_ports=(Port("topic"),),
                output_ports=(Port("report"),),
                tools=("reverse", "filesystem"),
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="build", mapping=(("topic", "topic"),)),),
        initial_state=(("topic", "hi"),),
        tool_refs=(("reverse", "mytools:make_reverse"),),
    )


def test_generate_tool_manifest_registers_and_imports() -> None:
    src = generate(_make_tool_manifest_wf())
    assert "from mytools import make_reverse as _tool_0" in src
    # default_registry / bind_tool are inlined into the module, not imported from flow.
    assert "def default_registry" in src and "def bind_tool" in src
    assert "from flow.tools import" not in src
    assert "_REGISTRY.register(bind_tool(_tool_0, 'reverse'))" in src
    assert '_REGISTRY.resolve(("reverse", "filesystem",))' in src


def test_generate_tool_manifest_ruff_clean() -> None:
    ok, msg = _ruff_clean(generate(_make_tool_manifest_wf()))
    assert ok, msg


def test_generate_no_manifest_unchanged_registry_line() -> None:
    """Workflows without a manifest keep the bare registry line (no extra imports)."""
    src = generate(_make_linear_wf())
    assert "_REGISTRY = default_registry()\n" in src
    # No custom-tool registration is emitted without a manifest.
    assert "bind_tool(_tool_" not in src
    assert "from flow.tools import" not in src


# ---------------------------------------------------------------------------
# retry policy
# ---------------------------------------------------------------------------


def test_generate_no_retry_source_unchanged() -> None:
    """A workflow with no retry policy emits the same core-call line as before."""
    wf = WorkflowDef(
        name="no-retry",
        provider="copilot",
        entry="work",
        default_model="m",
        nodes=(
            NodeDef(
                id="work",
                type="script",
                code="def work(ctx):\n    return 'done'",
                output_ports=(Port("out", "string"),),
            ),
        ),
        edges=(),
    )
    src = generate(wf)
    # The pure node function calls the inlined script with its ctx param.
    assert "_val = _script_work(ctx)" in src
    # A no-retry node drives with retry_max=1 (the retry loop lives in _drive).
    assert "retry_max=1" in src


async def test_generate_retry_script_node_retries_on_failure() -> None:
    """Generated module retries a failing script node up to retry.max times."""
    import types

    # The inline script uses a module-level counter to fail on the first N calls.
    # We embed N=2 (retry.max=2 => max_attempts=3 => fails twice, succeeds on 3rd).
    script_code = (
        "_CALL_COUNT = 0\n"
        "def flaky(ctx):\n"
        "    global _CALL_COUNT\n"
        "    _CALL_COUNT += 1\n"
        "    if _CALL_COUNT < 3:\n"
        "        raise RuntimeError(f'fail {_CALL_COUNT}')\n"
        "    return 'ok'\n"
    )
    wf = WorkflowDef(
        name="retry-wf",
        provider="copilot",
        entry="flaky",
        default_model="m",
        nodes=(
            NodeDef(
                id="flaky",
                type="script",
                code=script_code,
                output_ports=(Port("result", "string"),),
                retry=RetryPolicy(max=2, backoff=0.0),
            ),
        ),
        edges=(),
    )
    src = generate(wf)
    ok, msg = _ruff_clean(src)
    assert ok, f"generated code not ruff-clean:\n{src}\n{msg}"

    gen_module = types.ModuleType("_retry_test_module")
    exec(compile(src, "<retry_test>", "exec"), gen_module.__dict__)  # noqa: S102
    await gen_module.main()  # type: ignore[attr-defined]

    # The node ran 3 times total (failed twice, succeeded on 3rd).
    assert gen_module._CALL_COUNT == 3  # type: ignore[attr-defined]
    assert gen_module._OUT["flaky"]["result"] == "ok"  # type: ignore[attr-defined]


async def test_generate_retry_exhausted_raises() -> None:
    """Generated module propagates the last exception when all attempts fail."""
    import types

    script_code = "def always_fail(ctx):\n    raise ValueError('boom')\n"
    wf = WorkflowDef(
        name="retry-fail-wf",
        provider="copilot",
        entry="always_fail",
        default_model="m",
        nodes=(
            NodeDef(
                id="always_fail",
                type="script",
                code=script_code,
                output_ports=(Port("result", "string"),),
                retry=RetryPolicy(max=1, backoff=0.0),
            ),
        ),
        edges=(),
    )
    src = generate(wf)
    ok, msg = _ruff_clean(src)
    assert ok, f"generated code not ruff-clean:\n{src}\n{msg}"

    gen_module = types.ModuleType("_retry_fail_test_module")
    exec(compile(src, "<retry_fail_test>", "exec"), gen_module.__dict__)  # noqa: S102
    import pytest as _pytest

    with _pytest.raises(ValueError, match="boom"):
        await gen_module.main()  # type: ignore[attr-defined]


async def test_generate_isolate_node_reraises_budget_exceeded() -> None:
    """An isolate node must NOT swallow a WorkflowBudgetExceeded (control-flow).

    Regression for the codegen budget-swallow bug: the isolate node's
    ``except BaseException`` used to capture the budget breach into ``_ISOLATED``
    instead of aborting the run, diverging from the interpreter (which
    special-cases budget/pause at the scheduler level).
    """
    import types

    # The script raises the generated module's own inlined WorkflowBudgetExceeded
    # (available in the module globals) — the same class the isolate handler checks.
    script_code = (
        "def over_budget(ctx):\n"
        "    raise WorkflowBudgetExceeded(100, 10)\n"
    )
    wf = WorkflowDef(
        name="isolate-budget-wf",
        provider="copilot",
        entry="over_budget",
        default_model="m",
        nodes=(
            NodeDef(
                id="over_budget",
                type="script",
                code=script_code,
                output_ports=(Port("result", "string"),),
                on_error="isolate",
            ),
        ),
        edges=(),
    )
    src = generate(wf)
    ok, msg = _ruff_clean(src)
    assert ok, f"generated code not ruff-clean:\n{src}\n{msg}"
    # WorkflowBudgetExceeded is inlined into the module (not imported from flow).
    assert "class WorkflowBudgetExceeded" in src
    assert "from flow.errors import" not in src

    gen_module = types.ModuleType("_isolate_budget_test_module")
    exec(compile(src, "<isolate_budget_test>", "exec"), gen_module.__dict__)  # noqa: S102
    import pytest as _pytest

    # The control-flow exception propagates instead of being isolated.
    with _pytest.raises(gen_module.WorkflowBudgetExceeded):  # type: ignore[attr-defined]
        await gen_module.main()  # type: ignore[attr-defined]
    # The node was NOT captured as an isolated failure — the run aborted instead.
    assert "over_budget" not in gen_module._ISOLATED  # type: ignore[attr-defined]
    assert "over_budget" not in gen_module._FAILED  # type: ignore[attr-defined]


def test_generate_checkpoint_persists_tokens_used() -> None:
    """The generated _save_checkpoint serializes tokens_used (budget-on-resume).

    Mirrors the interpreter's checkpoint schema so a run resumed across engines
    keeps its cumulative spend instead of resetting to zero.
    """
    src = generate(_make_linear_wf())
    assert '"tokens_used": _TOKENS_USED,' in src
    # ...and _load_checkpoint restores it back into the running total.
    assert 'global _TOKENS_USED' in src
    assert '_snap.get("tokens_used")' in src


def _make_output_schema_wf() -> WorkflowDef:
    """A single agent node with output_schema + web_search."""
    return WorkflowDef(
        name="schema_ws_wf",
        provider="copilot",
        entry="n1",
        default_model="m",
        nodes=(
            NodeDef(
                id="n1",
                model="m",
                system_prompt="Summarise.",
                prompt="summarise {{topic}}",
                input_ports=(Port("topic"),),
                # Single structured port: the whole submitted object lands in "out".
                output_ports=(
                    Port(
                        "out",
                        schema={
                            "type": "object",
                            "properties": {"summary": {"type": "string"}, "score": {"type": "integer"}},
                        },
                    ),
                ),
                web_search=True,
                web_search_model="sonar",
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="n1", mapping=(("topic", "topic"),)),),
        initial_state=(("topic", "flow"),),
    )


def test_generate_output_schema_call_site_and_ruff_clean() -> None:
    """output_schema + web_search reach the generated _run_agent call and stay ruff-clean."""
    src = generate(_make_output_schema_wf())
    # the submit_result directive path is compiled in (via the template helper)
    assert "create_submit_result_tool" in src
    assert "web_search_fn_from_provider" in src
    # the node's call site forwards the derived JSON schema and the search model
    assert "output_schema={'type': 'object'" in src
    assert "'summary': {'type': 'string'}" in src
    assert "'score': {'type': 'integer'}" in src
    assert 'web_search_model="sonar"' in src
    ok, msg = _ruff_clean(src)
    assert ok, f"generated code not ruff-clean:\n{src}\n{msg}"


async def test_generate_output_schema_parity_with_interpreter() -> None:
    """A generated output_schema module emits the same JSON output as the interpreter.

    Both engines register submit_result, the (stubbed) agent submits an object, and
    each serializes it as sorted JSON into the node's output port. web_search is
    enabled but the stub agent never invokes it, so it is inert here.
    """
    import types

    import agent.helpers as _agent_helpers
    import ai as _ai
    from ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
    from ai.utils.event_stream import EventStream as AiEventStream
    from flow.executor import execute

    result_obj = {"summary": "all good", "score": 42}

    def _submit_stream_fn(model_id: object, context: object, options: object = None) -> "AiEventStream[AssistantMessage]":  # noqa: E501
        stream: AiEventStream[AssistantMessage] = AiEventStream()
        # First turn: emit a submit_result tool call; the Agent executes it,
        # populating the result sink via tool_ctx. Second turn: stop.
        if not getattr(_submit_stream_fn, "_called", False):
            _submit_stream_fn._called = True  # type: ignore[attr-defined]
            call = ToolCall(id="tc-1", name="submit_result", arguments={"result": result_obj})
            msg = AssistantMessage(content=(call,), stop_reason="toolUse")
        else:
            msg = AssistantMessage(content=(TextContent(text="done"),), stop_reason="stop")

        async def _push() -> None:
            await asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason=msg.stop_reason, message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())  # noqa: RUF006
        return stream

    wf = _make_output_schema_wf()
    src = generate(wf)

    original_sfp = _agent_helpers.stream_fn_from_provider
    original_ai_provider = _ai.provider
    # The template calls web_search_fn_from_provider only when a search model is set;
    # the stub agent never calls the tool, so a no-op factory is enough to stay import-safe.
    original_ws = _agent_helpers.web_search_fn_from_provider
    _agent_helpers.stream_fn_from_provider = lambda _p: _submit_stream_fn  # type: ignore[assignment]

    async def _noop_ws(_q: str) -> str:
        return ""

    _agent_helpers.web_search_fn_from_provider = lambda _p, _m: _noop_ws  # type: ignore[assignment]
    _ai.provider = lambda _name: object()  # type: ignore[assignment]
    gen_module = types.ModuleType("_schema_parity_module")
    try:
        _submit_stream_fn._called = False  # type: ignore[attr-defined]
        exec(compile(src, "<schema_parity>", "exec"), gen_module.__dict__)  # noqa: S102
        await gen_module.main()  # type: ignore[attr-defined]
        gen_out = gen_module._RUNTIME["state"]["n1"]["out"]  # type: ignore[attr-defined]

        def _interp_factory(model: str) -> object:
            _submit_stream_fn._called = False  # type: ignore[attr-defined]
            return _submit_stream_fn

        interp = await execute(wf, stream_fn_factory=_interp_factory)  # type: ignore[arg-type]
        interp_out = interp.runtime["state"]["n1"]["out"]
    finally:
        _agent_helpers.stream_fn_from_provider = original_sfp  # type: ignore[assignment]
        _agent_helpers.web_search_fn_from_provider = original_ws  # type: ignore[assignment]
        _ai.provider = original_ai_provider  # type: ignore[assignment]

    # output_schema keeps the submitted object structured — a live dict in both engines.
    assert gen_out == result_obj
    assert gen_out == interp_out  # both engines hold the same live object


async def test_generated_module_does_not_import_flow_internal() -> None:
    """The generated module never imports the flow package at all.

    errors / coerce / runtime AND the tool registry (default_registry / bind_tool /
    ToolRegistry) are inlined into the module source, so a generated workflow is
    self-contained with respect to the flow package.
    """
    import types

    # A script node exercises to_python / to_state / RuntimeContext. This is a
    # pure-script workflow (no agent nodes).
    wf = WorkflowDef(
        name="no-flow-internal",
        provider="copilot",
        entry="s",
        default_model="m",
        nodes=(
            NodeDef(
                id="s",
                type="script",
                code="def s(ctx, n):\n    return n + 1\n",
                input_ports=(Port("n", "integer"),),
                output_ports=(Port("o", "integer"),),
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="s", mapping=(("n", "n"),)),),
        initial_state=(("n", "41"),),
    )
    src = generate(wf)

    # No import of the flow package whatsoever.
    assert "from flow." not in src
    assert "import flow" not in src
    # The helpers are inlined instead.
    assert "def to_python" in src and "def to_state" in src
    assert "class RuntimeContext" in src
    # This is a SCRIPT-ONLY workflow, so the SDK tool registry + agent/ai imports
    # are omitted (only an SDK agent node needs them); see docs/cli-agent.md.
    assert "def default_registry" not in src and "class ToolRegistry" not in src
    assert "import ai" not in src and "from agent" not in src

    # And the inlined coercion actually works end to end (41 -> "42").
    ok, msg = _ruff_clean(src)
    assert ok, f"generated code not ruff-clean:\n{src}\n{msg}"
    gen_module = types.ModuleType("_no_flow_internal_module")
    exec(compile(src, "<no_flow_internal>", "exec"), gen_module.__dict__)  # noqa: S102
    await gen_module.main()  # type: ignore[attr-defined]
    assert gen_module._OUT["s"]["o"] == 42  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Structured (type-native) wire format + nested interpolation.
#
# Gate test for the wire-format change: a script node emits an ``object`` port
# holding a real dict; a downstream node reads a NESTED field via
# ``{{plan.owner}}`` interpolation.  Today the port is flattened to a JSON string
# by to_state and ``{{plan.owner}}`` cannot reach a field, so both the
# "port is a real dict" and the nested-interpolation assertions fail.  After the
# change the interpreter and the generated module must agree, and the structured
# port must hold a live dict (not a serialized string).
# ---------------------------------------------------------------------------


def _make_structured_wire_wf() -> WorkflowDef:
    """plan (script, object port) -> render (script, reads {{plan.owner}})."""
    return WorkflowDef(
        name="structured-wire",
        provider="copilot",
        entry="plan",
        default_model="m",
        nodes=(
            NodeDef(
                id="plan",
                type="script",
                code=(
                    "def plan(ctx, topic):\n"
                    "    return {'owner': 'ada', 'tasks': ['spec', topic]}\n"
                ),
                input_ports=(Port("topic", "string"),),
                output_ports=(Port("plan", "object"),),
            ),
            NodeDef(
                id="render",
                type="script",
                # The script receives the object port as a live dict and pulls a
                # nested field; the prompt-style nested interpolation is exercised
                # by the agent-free assertion below on the interpreter side.
                code=(
                    "def render(ctx, plan):\n"
                    "    return f\"{plan['owner']}:{plan['tasks'][1]}\"\n"
                ),
                input_ports=(Port("plan", "object"),),
                output_ports=(Port("line", "string"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="plan", mapping=(("topic", "topic"),)),
            EdgeDef(src="plan", dst="render", mapping=(("plan", "plan"),)),
        ),
        initial_state=(("topic", "ship"),),
    )


async def test_structured_port_is_live_object_parity() -> None:
    """An ``object`` port carries a real dict through both engines (not a JSON string)."""
    from flow.executor import execute

    wf = _make_structured_wire_wf()

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    # The structured port must be a LIVE dict, not a serialized JSON string.
    assert gen_state["plan"]["plan"] == {"owner": "ada", "tasks": ["spec", "ship"]}
    assert isinstance(gen_state["plan"]["plan"], dict)
    # The downstream script read the nested field from the live object.
    assert gen_state["render"]["line"] == "ada:ship"
    # interpret == compile.
    assert gen_state == _interp_out(run_result)


async def test_nested_interpolation_in_prompt_parity() -> None:
    """``{{plan.owner}}`` / ``{{plan.tasks.0}}`` resolve a field from a structured port.

    A downstream script's *input* is a string port fed from the object port via a
    condition-free edge, but the interpolation grammar itself is exercised through
    a guard: the edge to ``gate`` fires only when ``{{plan.owner}}`` equals ``ada``.
    """
    from flow.executor import execute

    wf = WorkflowDef(
        name="nested-interp",
        provider="copilot",
        entry="plan",
        default_model="m",
        nodes=(
            NodeDef(
                id="plan",
                type="script",
                code=(
                    "def plan(ctx, topic):\n"
                    "    return {'owner': 'ada', 'tasks': ['spec', topic]}\n"
                ),
                input_ports=(Port("topic", "string"),),
                output_ports=(Port("plan", "object"),),
            ),
            NodeDef(
                id="gate",
                type="script",
                code="def gate(ctx):\n    return 'reached'",
                output_ports=(Port("mark", "string"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="plan", mapping=(("topic", "topic"),)),
            # Guard uses a NESTED field of the structured source port.
            EdgeDef(
                src="plan",
                dst="gate",
                when=Condition(op="equals", value="{{plan.owner}}", text="ada"),
            ),
        ),
        initial_state=(("topic", "ship"),),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    # owner == "ada" so the gate fires in both engines.
    assert gen_state["gate"]["mark"] == "reached"
    assert gen_state == _interp_out(run_result)


async def test_structured_initial_state_parity() -> None:
    """A structured ``$in`` seed (list/object) flows type-native through both engines."""
    from flow.executor import execute

    wf = WorkflowDef(
        name="structured-seed",
        provider="copilot",
        entry="use",
        default_model="m",
        nodes=(
            NodeDef(
                id="use",
                type="script",
                code="def use(ctx, cfg, items):\n    return {'first': items[0], 'flag': cfg['on']}",
                input_ports=(Port("cfg", "object"), Port("items", "array")),
                output_ports=(Port("r", "object"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="use", mapping=(("cfg", "cfg"), ("items", "items"))),
        ),
        # Type-native seed: a dict and a list, not strings.
        initial_state=(("cfg", {"on": True}), ("items", [10, 20])),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    assert gen_state["use"]["r"] == {"first": 10, "flag": True}
    assert gen_state["$in"]["cfg"] == {"on": True}
    assert gen_state == _interp_out(run_result)


def _make_multi_output_agent_wf() -> WorkflowDef:
    """An output_schema agent that fans its fields out to SEPARATE output ports."""
    return WorkflowDef(
        name="multi-out-agent",
        provider="copilot",
        entry="plan",
        default_model="m",
        nodes=(
            NodeDef(
                id="plan",
                model="m",
                system_prompt="Plan.",
                prompt="plan {{topic}}",
                input_ports=(Port("topic"),),
                # THREE declared output ports, each typed, each named after a schema field.
                output_ports=(
                    Port("summary", "string"),
                    Port("tasks", "array"),
                    Port("cost", "number"),
                ),
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="plan", mapping=(("topic", "topic"),)),),
        initial_state=(("topic", "ship"),),
    )


def _multi_field_submit_stream_fn(result_obj: dict[str, object]) -> object:
    """Build a stub stream_fn that submits *result_obj* via submit_result once."""
    import asyncio as _asyncio

    from ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
    from ai.utils.event_stream import EventStream as AiEventStream

    def _fn(model_id: object, context: object, options: object = None) -> "AiEventStream[AssistantMessage]":
        stream: AiEventStream[AssistantMessage] = AiEventStream()
        if not getattr(_fn, "_called", False):
            _fn._called = True  # type: ignore[attr-defined]
            call = ToolCall(id="tc-1", name="submit_result", arguments={"result": result_obj})
            msg = AssistantMessage(content=(call,), stop_reason="toolUse")
        else:
            msg = AssistantMessage(content=(TextContent(text="done"),), stop_reason="stop")

        async def _push() -> None:
            await _asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason=msg.stop_reason, message=msg))
            stream.set_result(msg)
            await stream.close()

        _asyncio.ensure_future(_push())  # noqa: RUF006
        return stream

    return _fn


async def test_generate_multi_output_agent_parity() -> None:
    """A multi-port output_schema agent fans fields to separate, type-native ports.

    Both engines must store ``plan.summary`` (str), ``plan.tasks`` (list), and
    ``plan.cost`` (float) as INDEPENDENT ports — not one packed object.
    """
    import types

    import agent.helpers as _agent_helpers
    import ai as _ai
    from flow.executor import execute

    result_obj: dict[str, object] = {"summary": "do it", "tasks": ["a", "b"], "cost": 42.5}
    wf = _make_multi_output_agent_wf()
    src = generate(wf)

    stub = _multi_field_submit_stream_fn(result_obj)
    original_sfp = _agent_helpers.stream_fn_from_provider
    original_ai_provider = _ai.provider
    _agent_helpers.stream_fn_from_provider = lambda _p: stub  # type: ignore[assignment]
    _ai.provider = lambda _name: object()  # type: ignore[assignment]
    gen_module = types.ModuleType("_multi_out_module")
    try:
        stub._called = False  # type: ignore[attr-defined]
        exec(compile(src, "<multi_out>", "exec"), gen_module.__dict__)  # noqa: S102
        await gen_module.main()  # type: ignore[attr-defined]
        gen_state = {k: dict(v) for k, v in gen_module._OUT.items()}  # type: ignore[attr-defined]

        def _factory(model: str) -> object:
            stub._called = False  # type: ignore[attr-defined]
            return stub

        interp = await execute(wf, stream_fn_factory=_factory)  # type: ignore[arg-type]
    finally:
        _agent_helpers.stream_fn_from_provider = original_sfp  # type: ignore[assignment]
        _ai.provider = original_ai_provider  # type: ignore[assignment]

    # Each field landed in its OWN port, type-native.
    assert gen_state["plan"]["summary"] == "do it"
    assert gen_state["plan"]["tasks"] == ["a", "b"]
    assert gen_state["plan"]["cost"] == 42.5
    assert isinstance(gen_state["plan"]["tasks"], list)
    assert isinstance(gen_state["plan"]["cost"], float)
    # interpret == compile.
    assert gen_state == _interp_out(interp)


async def test_generate_subfield_mapping_parity() -> None:
    """An edge maps a NESTED field of a structured source port to a downstream input.

    ``plan`` (script) emits an ``object`` port; the edge to ``use`` maps
    ``plan.owner`` -> ``who`` and ``plan.tasks`` -> ``items``.  Both engines must
    resolve the sub-fields identically.
    """
    from flow.executor import execute

    wf = WorkflowDef(
        name="subfield-map",
        provider="copilot",
        entry="plan",
        default_model="m",
        nodes=(
            NodeDef(
                id="plan",
                type="script",
                code="def plan(ctx, topic):\n    return {'owner': 'ada', 'tasks': ['spec', topic]}",
                input_ports=(Port("topic", "string"),),
                output_ports=(Port("plan", "object"),),
            ),
            NodeDef(
                id="use",
                type="script",
                code="def use(ctx, who, items):\n    return f\"{who}:{items[1]}\"",
                input_ports=(Port("who", "string"), Port("items", "array")),
                output_ports=(Port("line", "string"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="plan", mapping=(("topic", "topic"),)),
            # Sub-field mapping: source keys carry a dotted path into the object port.
            EdgeDef(src="plan", dst="use", mapping=(("plan.owner", "who"), ("plan.tasks", "items"))),
        ),
        initial_state=(("topic", "ship"),),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    assert gen_state["use"]["line"] == "ada:ship"
    assert gen_state == _interp_out(run_result)


async def test_generate_derived_entry_multi_parallel_parity() -> None:
    """With no explicit entry, all $in-only nodes are entries and run in parallel."""
    from flow.executor import execute

    wf = WorkflowDef(
        name="derived-entry",
        provider="copilot",
        entry="",  # no explicit entry -> derive from the $in frontier
        default_model="m",
        nodes=(
            NodeDef(
                id="a",
                type="script",
                code="def a(ctx, n):\n    return n + 1",
                input_ports=(Port("n", "integer"),),
                output_ports=(Port("oa", "integer"),),
            ),
            NodeDef(
                id="b",
                type="script",
                code="def b(ctx, n):\n    return n * 10",
                input_ports=(Port("n", "integer"),),
                output_ports=(Port("ob", "integer"),),
            ),
            NodeDef(
                id="c",
                type="script",
                code="def c(ctx, oa, ob):\n    return oa + ob",
                input_ports=(Port("oa", "integer"), Port("ob", "integer")),
                output_ports=(Port("oc", "integer"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="a", mapping=(("n", "n"),)),
            EdgeDef(src=IN_NODE_ID, dst="b", mapping=(("n", "n"),)),
            EdgeDef(src="a", dst="c", mapping=(("oa", "oa"),)),
            EdgeDef(src="b", dst="c", mapping=(("ob", "ob"),)),
        ),
        initial_state=(("n", 5),),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    # a=6, b=50, c=56 — both a and b are entries (only depend on $in).
    assert gen_state["c"]["oc"] == 56
    assert gen_state == _interp_out(run_result)


async def test_generate_flow_inputs_override_parity() -> None:
    """FLOW_INPUTS overrides $in in the generated module exactly like execute(inputs=)."""
    import json as _json
    import os

    from flow.executor import execute

    wf = WorkflowDef(
        name="override",
        provider="copilot",
        entry="dbl",
        default_model="m",
        nodes=(
            NodeDef(
                id="dbl",
                type="script",
                code="def dbl(ctx, n):\n    return n * 2",
                input_ports=(Port("n", "integer"),),
                output_ports=(Port("out", "integer"),),
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="dbl", mapping=(("n", "n"),)),),
        initial_state=(("n", 5),),
    )
    override = {"n": 21}

    # Generated: set FLOW_INPUTS, run the module.
    src = generate(wf)
    ok, msg = _ruff_clean(src)
    assert ok, f"generated not ruff-clean:\n{msg}"
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(src)
        tmp = Path(f.name)
    _prev = os.environ.get("FLOW_INPUTS")
    try:
        os.environ["FLOW_INPUTS"] = _json.dumps(override)
        spec = importlib.util.spec_from_file_location(f"gen_{uuid.uuid4().hex}", tmp)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        await mod.main()
        gen_state = {k: dict(v) for k, v in mod._OUT.items()}
    finally:
        if _prev is None:
            os.environ.pop("FLOW_INPUTS", None)
        else:
            os.environ["FLOW_INPUTS"] = _prev
        tmp.unlink(missing_ok=True)

    # Interpreter: same override via execute(inputs=).
    interp = await execute(wf, inputs=override)

    assert gen_state["dbl"]["out"] == 42  # 21 * 2, the override took effect
    assert gen_state == _interp_out(interp)


async def test_generate_numeric_condition_parity() -> None:
    """A gte/lt loop-exit guard behaves identically in both engines."""
    from flow.executor import execute

    # bump increments a counter; the back-edge loops while count < 3 (numeric lt),
    # so the loop runs until count reaches 3.
    wf = WorkflowDef(
        name="numeric-loop",
        provider="copilot",
        entry="bump",
        default_model="m",
        nodes=(
            NodeDef(
                id="bump",
                type="script",
                code="def bump(ctx, count):\n    return count + 1",
                input_ports=(Port("count", "integer"),),
                output_ports=(Port("count", "integer"),),
            ),
            NodeDef(
                id="done",
                type="script",
                code="def done(ctx, count):\n    return f'final:{count}'",
                input_ports=(Port("count", "integer"),),
                output_ports=(Port("result", "string"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="bump", mapping=(("count", "count"),)),
            # loop back to bump while count < 3 (numeric)
            EdgeDef(
                src="bump", dst="bump", mapping=(("count", "count"),),
                when=Condition(op="lt", value="{{$.count}}", text="3"),
                loop_max=5,
            ),
            EdgeDef(src="bump", dst="done", mapping=(("count", "count"),)),
        ),
        initial_state=(("count", 0),),
    )

    gen_state = await _run_generated(wf)
    run_result = await execute(wf)

    # 0 -> 1 -> 2 -> 3 (loop stops when count reaches 3, no longer < 3)
    assert gen_state["done"]["result"] == "final:3"
    assert gen_state == _interp_out(run_result)
