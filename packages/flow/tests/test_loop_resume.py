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

import pytest
from flow.builder.serialize import workflow_to_dict
from flow.checkpoint import JSONFileCheckpointStore
from flow.codegen import generate
from flow.executor import execute
from flow.loader import parse_workflow
from flow.models import _resolve_edge_ids, edge_identities


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
