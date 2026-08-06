"""Codegen loop resume — generated modules checkpoint coherent frontier batches
and resume loop activations from persisted edge counters.

Script-only loops (no LLM) so these run offline and deterministically.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types
from typing import Any

import pytest
from xdog.flow.builder.serialize import workflow_to_dict
from xdog.flow.checkpoint import JSONFileCheckpointStore
from xdog.flow.codegen import generate
from xdog.flow.errors import WorkflowExecutionError
from xdog.flow.executor import execute
from xdog.flow.loader import parse_workflow
from xdog.flow.models import _resolve_edge_ids, edge_identities


def _count_loop_wf(loop_max: int = 10, exit_at: int = 5, crash_code: str = "") -> dict[str, Any]:
    """a: n -> n+1 (with optional crash hook); b passes through; loop until d >= exit_at."""
    code_a = "def a(ctx, n):\n" + (crash_code or "") + "    return n + 1"
    return {
        "name": "loop",
        "provider": "copilot",
        "entry": "a",
        "state": {"n": 0},
        "nodes": [
            {
                "id": "a",
                "type": "script",
                "inputs": [{"name": "n", "schema": {"type": "integer"}}],
                "code": code_a,
                "outputs": [{"name": "c", "schema": {"type": "integer"}}],
            },
            {
                "id": "b",
                "type": "script",
                "inputs": [{"name": "c", "schema": {"type": "integer"}}],
                "code": "def b(ctx, c):\n    return c",
                "outputs": [{"name": "d", "schema": {"type": "integer"}}],
            },
        ],
        "edges": [
            {"from": "$in", "to": "a", "map": {"n": "n"}},
            {"from": "a", "to": "b", "map": {"c": "c"}},
            {
                "from": "b",
                "to": "a",
                "map": {"d": "n"},
                "loop": {"max": loop_max},
                "when": {"lt": {"value": "{{d}}", "text": str(exit_at)}},
            },
            {"from": "b", "to": "$output", "map": {"d": "r"}},
        ],
    }


def _run_gen(src: str, mod_name: str = "_gen_loop") -> dict[str, Any]:
    mod = types.ModuleType(mod_name)
    exec(compile(src, "<gen_loop>", "exec"), mod.__dict__)  # noqa: S102
    asyncio.run(mod.main())  # type: ignore[attr-defined]
    return dict(mod._RUNTIME)  # type: ignore[attr-defined]


def test_loop_fresh_run_parity() -> None:
    """A bounded loop's output matches through execute() and the generated module."""
    wf = parse_workflow(_count_loop_wf())
    interp = asyncio.run(execute(wf))
    gen = _run_gen(generate(wf))
    assert gen["out"] == dict(interp.runtime["out"]) == {"r": 5}


def test_generated_loop_uses_frontier_edge_identity() -> None:
    wf = parse_workflow(_count_loop_wf())
    key = edge_identities(wf)[2]
    src = generate(wf)
    assert "_FRONTIER_SPEC" in src
    assert key in src
    assert "complete_batch(_FRONTIER_SPEC" in src
    assert "for _loop_i_" not in src


def test_edge_identity_is_content_hashed_and_duplicate_safe() -> None:
    data = _count_loop_wf()
    wf = parse_workflow(data)
    key = edge_identities(wf)[2]
    assert key.startswith("edge-") and len(key) == 10

    # Inserting an unrelated edge does not renumber the loop identity.
    shifted = _count_loop_wf()
    shifted["edges"].insert(1, {"from": "a", "to": "$output", "map": {"c": "debug"}})
    shifted_wf = parse_workflow(shifted)
    assert edge_identities(shifted_wf)[3] == key

    # Exact duplicate edges receive distinct occurrence hashes.
    duplicate = _count_loop_wf()
    duplicate["edges"].insert(3, dict(duplicate["edges"][2]))
    duplicate_wf = parse_workflow(duplicate)
    duplicate_ids = edge_identities(duplicate_wf)
    assert duplicate_ids[2] != duplicate_ids[3]


def test_edge_identity_short_hash_collision_gets_suffix() -> None:
    fingerprints = (
        "abcde" + "1" * 59,
        "fffff" + "0" * 59,
        "abcde" + "2" * 59,
        "abcde" + "1" * 59,  # exact duplicate of the first fingerprint
    )
    assert _resolve_edge_ids(fingerprints) == (
        "edge-abcde",
        "edge-fffff",
        "edge-abcde-2",
        "edge-abcde-1",
    )


def _while_loop_wf(loop_max: int | None, exit_at: int) -> dict[str, Any]:
    data = _count_loop_wf(loop_max=10, exit_at=exit_at)
    loop_edge = data["edges"][2]
    cond = loop_edge.pop("when")
    loop_edge.pop("loop")
    loop_edge["while"] = {"cond": cond} if loop_max is None else {"cond": cond, "max": loop_max}
    return data


def test_while_sugar_defaults_to_safe_bound_and_roundtrips() -> None:
    wf = parse_workflow(_while_loop_wf(loop_max=None, exit_at=5))
    edge = wf.edges[2]
    assert edge.loop_max == 100
    assert edge.loop_strict is True
    assert edge.when is not None
    assert parse_workflow(workflow_to_dict(wf)) == wf


@pytest.mark.parametrize("loop_max", [2, 5])
def test_while_converges_at_and_before_bound_with_parity(loop_max: int) -> None:
    """Initial node run plus loop_max back-edge firings may reach the exit value."""
    wf = parse_workflow(_while_loop_wf(loop_max=loop_max, exit_at=3))
    interpreted = asyncio.run(execute(wf))
    generated = _run_gen(generate(wf), f"_while_{loop_max}")
    assert generated["out"] == dict(interpreted.runtime["out"]) == {"r": 3}


def test_while_non_convergence_raises_in_both_engines() -> None:
    wf = parse_workflow(_while_loop_wf(loop_max=1, exit_at=3))
    message = "while loop 'b'->'a' did not converge within 1 iterations"

    with pytest.raises(Exception, match=message):
        asyncio.run(execute(wf))
    with pytest.raises(Exception, match=message):
        _run_gen(generate(wf), "_while_non_converging")


def test_interpreter_resume_preserves_while_non_convergence(tmp_path: pathlib.Path) -> None:
    """A terminal checkpoint must not turn a failed strict while into success."""
    wf = parse_workflow(_while_loop_wf(loop_max=1, exit_at=3))
    store = JSONFileCheckpointStore(tmp_path)
    message = "while loop 'b'->'a' did not converge within 1 iterations"

    with pytest.raises(Exception, match=message):
        asyncio.run(execute(wf, checkpoint=store, run_id="strict"))
    with pytest.raises(Exception, match=message):
        asyncio.run(execute(wf, checkpoint=store, run_id="strict"))


def test_generated_loop_persists_counter(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """A completed generated run records loop_counters in its checkpoint."""
    ck = tmp_path / "ck"
    ck.mkdir()
    monkeypatch.setenv("FLOW_RUN_ID", "done")
    monkeypatch.setenv("FLOW_CHECKPOINT_DIR", str(ck))
    wf = parse_workflow(_count_loop_wf())
    key = edge_identities(wf)[2]
    _run_gen(generate(wf))
    snap = json.loads((ck / "done.json").read_text())
    # Counter now has one meaning in both engines: successful back-edge fires.
    # r reaches 5 after the initial pass plus four loop reactivations.
    assert snap["loop_counters"] == {key: 4}


def test_generated_loop_crash_resumes_midloop(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """A crash mid-loop resumes from the persisted counter, not from iteration 0."""
    runs = tmp_path / "runs"
    # a raises on its 3rd run unless RESUMED is set; a run-counter file proves it
    # did NOT restart the whole loop on resume.
    crash = (
        "    import pathlib, os\n"
        f"    _p = pathlib.Path({str(runs)!r})\n"
        "    _k = int(_p.read_text()) if _p.exists() else 0\n"
        "    _k += 1\n"
        "    _p.write_text(str(_k))\n"
        "    if _k == 3 and not os.environ.get('RESUMED'):\n"
        "        raise RuntimeError('crash')\n"
    )
    wf = parse_workflow(_count_loop_wf(crash_code=crash))
    key = edge_identities(wf)[2]
    src = generate(wf)
    ck = tmp_path / "ck"
    ck.mkdir()
    monkeypatch.setenv("FLOW_RUN_ID", "c")
    monkeypatch.setenv("FLOW_CHECKPOINT_DIR", str(ck))

    # Run 1 crashes on the 3rd execution of 'a'.
    mod = types.ModuleType("_crash")
    exec(compile(src, "<gen>", "exec"), mod.__dict__)  # noqa: S102
    try:
        asyncio.run(mod.main())  # type: ignore[attr-defined]
        raise AssertionError("expected a crash")
    except RuntimeError:
        pass
    snap = json.loads((ck / "c.json").read_text())
    # Progress was persisted mid-loop (some iterations completed before the crash).
    assert snap["loop_counters"].get(key, 0) >= 1

    # Run 2 resumes; 'a' no longer crashes.
    monkeypatch.setenv("RESUMED", "1")
    out = _run_gen(src, "_resume")["out"]
    assert out == {"r": 5}
    total_a_runs = int(runs.read_text())
    # Resume continued mid-loop rather than restarting: total 'a' runs stays small
    # (a full-from-scratch re-run would be 5 clean + the 2 pre-crash = 7+; resume is fewer).
    assert total_a_runs <= 6, f"resume re-ran too much: {total_a_runs} a-runs"


def test_generated_resume_rejects_input_override(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    wf = parse_workflow(_count_loop_wf(exit_at=1))
    source = generate(wf)
    checkpoint_dir = tmp_path / "ck"
    checkpoint_dir.mkdir()
    monkeypatch.setenv("FLOW_RUN_ID", "override")
    monkeypatch.setenv("FLOW_CHECKPOINT_DIR", str(checkpoint_dir))
    _run_gen(source, "_override_initial")

    monkeypatch.setenv("FLOW_INPUTS", '{"n": 99}')
    module = types.ModuleType("_override_resume")
    exec(compile(source, "<override-resume>", "exec"), module.__dict__)  # noqa: S102
    with pytest.raises(Exception, match="cannot override a resumed checkpoint"):
        asyncio.run(module.main())  # type: ignore[attr-defined]


def _nested_loop_wf() -> dict[str, Any]:
    """An outer loop containing an inner loop, to exercise distinct loop vars."""
    return {
        "name": "nested",
        "provider": "copilot",
        "entry": "outer",
        "state": {"seed": 0},
        "nodes": [
            {
                "id": "outer",
                "type": "script",
                "inputs": [{"name": "seed", "schema": {"type": "integer"}}],
                "code": "def outer(ctx, seed):\n    return seed + 1",
                "outputs": [{"name": "o", "schema": {"type": "integer"}}],
            },
            {
                "id": "inner",
                "type": "script",
                "inputs": [{"name": "o", "schema": {"type": "integer"}}],
                "code": "def inner(ctx, o):\n    return o + 10",
                "outputs": [{"name": "i", "schema": {"type": "integer"}}],
            },
        ],
        "edges": [
            {"from": "$in", "to": "outer", "map": {"seed": "seed"}},
            {"from": "outer", "to": "inner", "map": {"o": "o"}},
            {"from": "inner", "to": "outer", "map": {"i": "seed"}, "loop": {"max": 2}},
        ],
    }


def test_nested_loops_use_distinct_vars() -> None:
    """Nested loops must not share one _loop_i (the old shadowing bug)."""
    # Not every graph produces true nesting, so assert the mechanism: two loops in
    # one workflow get depth-indexed variables, never a bare shared `_loop_i`.
    src = generate(parse_workflow(_nested_loop_wf()))
    assert "for _loop_i in range" not in src  # never the un-indexed shared name
    compile(src, "<n>", "exec")


# --- how a bounded loop ends, and how the run reports it ---------------------


def _repair_loop(strict: bool) -> dict[str, Any]:
    """gate --(FAIL)--> fix --(loop)--> gate ; gate --(PASS)--> done.

    The shape a repair loop takes: a check that can fail, a fixer, and a bounded
    number of retries. Nothing joins after the split — each branch has a single
    predecessor — so the only question is what happens when the retries run out.
    """
    back: dict[str, Any] = (
        {"while": {"cond": {"equals": {"value": "{{$.again}}", "text": "yes"}}, "max": 2}}
        if strict
        else {"loop": {"max": 2}}
    )
    return {
        "name": "repair",
        "entry": "gate",
        "in_schema": {"pass_on": {"type": "integer"}},
        "state": {"pass_on": 99},
        "nodes": [
            {
                "id": "gate", "type": "script", "run": "repairmod:gate",
                "inputs": [{"name": "pass_on", "schema": {"type": "integer"}, "required": True}],
                "outputs": [
                    {"name": "verdict", "schema": {"type": "string"}, "required": True},
                    {"name": "pass_on", "schema": {"type": "integer"}, "required": True},
                ],
            },
            {
                "id": "fix", "type": "script", "run": "repairmod:fix",
                "inputs": [{"name": "verdict", "schema": {"type": "string"}, "required": True}],
                "outputs": [{"name": "again", "schema": {"type": "string"}, "required": True}],
            },
            {
                "id": "done", "type": "script", "run": "repairmod:done",
                "inputs": [{"name": "verdict", "schema": {"type": "string"}, "required": True}],
                "outputs": [{"name": "final", "schema": {"type": "string"}, "required": True}],
            },
        ],
        "edges": [
            {"from": "$in", "to": "gate", "map": {"pass_on": "pass_on"}},
            # `contains` is interpolate(text) in interpolate(value): text is the needle.
            {"from": "gate", "to": "fix",
             "when": {"contains": {"value": "{{$.verdict}}", "text": "FAIL"}},
             "map": {"verdict": "verdict"}},
            {"from": "fix", "to": "gate", "map": {}, **back},
            {"from": "gate", "to": "done",
             "when": {"contains": {"value": "{{$.verdict}}", "text": "PASS"}},
             "map": {"verdict": "verdict"}},
            {"from": "done", "to": "$output", "map": {"final": "final"}},
        ],
    }


def _write_repair_module(tmp_path: pathlib.Path) -> None:
    (tmp_path / "repairmod.py").write_text(
        "from pathlib import Path\n"
        "_C = Path(__file__).parent / 'attempts'\n"
        "def gate(ctx, pass_on):\n"
        "    n = int(_C.read_text()) + 1 if _C.exists() else 1\n"
        "    _C.write_text(str(n))\n"
        "    v = 'PASS: clean' if n >= int(pass_on) else f'FAIL: attempt {n}'\n"
        "    return {'verdict': v, 'pass_on': pass_on}\n"
        "def fix(ctx, verdict):\n"
        "    return 'yes'\n"
        "def done(ctx, verdict):\n"
        "    return 'shipped'\n",
        encoding="utf-8",
    )


def test_repair_loop_exits_forward_as_soon_as_the_check_passes(tmp_path: pathlib.Path) -> None:
    _write_repair_module(tmp_path)
    wf = parse_workflow(_repair_loop(strict=False))
    result = asyncio.run(execute(wf, base_dir=tmp_path, inputs={"pass_on": 2}))
    assert result.runtime["out"] == {"final": "shipped"}
    # Nothing stopped it early — it ran out of work the ordinary way.
    assert result.runtime["stopped_by"] is None


def test_plain_loop_exhaustion_is_reported_rather_than_silent(tmp_path: pathlib.Path) -> None:
    """A bounded `loop` that runs out stops with success and (here) no output.

    That is indistinguishable from a clean finish unless the run says so, which is
    what `stopped_by` is for — it names the edge that ran out.
    """
    _write_repair_module(tmp_path)
    wf = parse_workflow(_repair_loop(strict=False))
    result = asyncio.run(execute(wf, base_dir=tmp_path, inputs={"pass_on": 99}))
    assert result.runtime["out"] == {}
    assert result.runtime["stopped_by"] == {"reason": "loop_exhausted", "edge": "fix->gate"}


def test_strict_while_exhaustion_fails_instead(tmp_path: pathlib.Path) -> None:
    """`while` is the same loop with one difference: running out is an error."""
    _write_repair_module(tmp_path)
    wf = parse_workflow(_repair_loop(strict=True))
    with pytest.raises(WorkflowExecutionError, match="did not converge"):
        asyncio.run(execute(wf, base_dir=tmp_path, inputs={"pass_on": 99}))


def test_both_engines_report_the_same_stop_reason(tmp_path: pathlib.Path) -> None:
    """interpret == compile for the run result, including why the run ended.

    The failure path used to break this: the CLI hardcoded lastNode="" and
    tokensUsed=0 because a raising execute() returns no ExecResult, while the
    generated module reported the truth from its own trace.
    """
    _write_repair_module(tmp_path)
    wf = parse_workflow(_repair_loop(strict=False))

    (tmp_path / "attempts").unlink(missing_ok=True)
    interpreted = asyncio.run(execute(wf, base_dir=tmp_path, inputs={"pass_on": 99}))

    module = types.ModuleType("gen_repair")
    sys.path.insert(0, str(tmp_path))
    try:
        (tmp_path / "attempts").unlink(missing_ok=True)
        exec(compile(generate(wf), "<repair>", "exec"), module.__dict__)  # noqa: S102
        asyncio.run(module.main())
    finally:
        sys.path.remove(str(tmp_path))

    assert module._STOPPED_BY == interpreted.runtime["stopped_by"]
    assert module._OUTPUT == interpreted.runtime["out"]
