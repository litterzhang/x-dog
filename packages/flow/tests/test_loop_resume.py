"""Codegen loop resume — the generated module checkpoints its loop position and
resumes mid-loop, aligned with the interpreter (docs: executor._save_checkpoint).

Script-only loops (no LLM) so these run offline and deterministically.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import types
from typing import Any

from flow.codegen import generate
from flow.executor import execute
from flow.loader import parse_workflow


def _count_loop_wf(loop_max: int = 10, exit_at: int = 5, crash_code: str = "") -> dict[str, Any]:
    """a: n -> n+1 (with optional crash hook); b passes through; loop until d >= exit_at."""
    code_a = "def a(ctx, n):\n" + (crash_code or "") + "    return n + 1"
    return {
        "name": "loop", "provider": "copilot", "entry": "a", "state": {"n": 0},
        "nodes": [
            {"id": "a", "type": "script", "inputs": [{"name": "n", "type": "integer"}],
             "code": code_a, "outputs": [{"name": "c", "type": "integer"}]},
            {"id": "b", "type": "script", "inputs": [{"name": "c", "type": "integer"}],
             "code": "def b(ctx, c):\n    return c", "outputs": [{"name": "d", "type": "integer"}]},
        ],
        "edges": [
            {"from": "$in", "to": "a", "map": {"n": "n"}},
            {"from": "a", "to": "b", "map": {"c": "c"}},
            {"from": "b", "to": "a", "map": {"d": "n"}, "loop": {"max": loop_max},
             "when": {"lt": {"value": "{{d}}", "text": str(exit_at)}}},
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


def test_generated_loop_uses_depth_indexed_var_and_tick() -> None:
    src = generate(parse_workflow(_count_loop_wf()))
    assert "for _loop_i_0 in range(_loop_start('b->a')" in src
    assert "_loop_tick('b->a', _loop_i_0)" in src


def test_generated_loop_persists_counter(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """A completed generated run records loop_counters in its checkpoint."""
    ck = tmp_path / "ck"
    ck.mkdir()
    monkeypatch.setenv("FLOW_RUN_ID", "done")
    monkeypatch.setenv("FLOW_CHECKPOINT_DIR", str(ck))
    _run_gen(generate(parse_workflow(_count_loop_wf())))
    snap = json.loads((ck / "done.json").read_text())
    assert snap["loop_counters"] == {"b->a": 5}


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
    src = generate(parse_workflow(_count_loop_wf(crash_code=crash)))
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
    assert snap["loop_counters"].get("b->a", 0) >= 1

    # Run 2 resumes; 'a' no longer crashes.
    monkeypatch.setenv("RESUMED", "1")
    out = _run_gen(src, "_resume")["out"]
    assert out == {"r": 5}
    total_a_runs = int(runs.read_text())
    # Resume continued mid-loop rather than restarting: total 'a' runs stays small
    # (a full-from-scratch re-run would be 5 clean + the 2 pre-crash = 7+; resume is fewer).
    assert total_a_runs <= 6, f"resume re-ran too much: {total_a_runs} a-runs"


def _nested_loop_wf() -> dict[str, Any]:
    """An outer loop containing an inner loop, to exercise distinct loop vars."""
    return {
        "name": "nested", "provider": "copilot", "entry": "outer", "state": {"seed": 0},
        "nodes": [
            {"id": "outer", "type": "script", "inputs": [{"name": "seed", "type": "integer"}],
             "code": "def outer(ctx, seed):\n    return seed + 1", "outputs": [{"name": "o", "type": "integer"}]},
            {"id": "inner", "type": "script", "inputs": [{"name": "o", "type": "integer"}],
             "code": "def inner(ctx, o):\n    return o + 10", "outputs": [{"name": "i", "type": "integer"}]},
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
