"""Tests for flow.codegen — port-model workflow code generation.

Generated modules keep node-private outputs in a nested ``_OUT[node][port]`` dict
(``$in`` + real nodes).  The regression tests assert the generated module and the
interpreter agree by comparing that dict to the interpreter's reconstructed
equivalent (``{$in: runtime["in"], **runtime["state"]}``).
"""

from __future__ import annotations

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
    assert 'ai.provider("anthropic")' in generate(_make_linear_wf())


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
    assert "range(3)" in src
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
    assert gen_state["inc"]["c"] == "3"
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

    assert gen_state["C"]["c"] == "66"
    assert gen_state["B"]["b"] == "60"
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
    assert "from flow.tools import bind_tool, default_registry" in src
    assert "_REGISTRY.register(bind_tool(_tool_0, 'reverse'))" in src
    assert '_REGISTRY.resolve(("reverse", "filesystem",))' in src


def test_generate_tool_manifest_ruff_clean() -> None:
    ok, msg = _ruff_clean(generate(_make_tool_manifest_wf()))
    assert ok, msg


def test_generate_no_manifest_unchanged_registry_line() -> None:
    """Workflows without a manifest keep the bare registry line (no extra imports)."""
    src = generate(_make_linear_wf())
    assert "_REGISTRY = default_registry()\n" in src
    assert "coerce_tool" not in src


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
    # The core-call line appears verbatim — no retry scaffolding.
    assert "_val = _script_work(_ctx)" in src
    assert "_last_exc" not in src
    assert "_attempt" not in src


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
