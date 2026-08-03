"""Sub-workflow (G5) — interpreter behaviour and cross-engine parity.

A ``type="subflow"`` node runs an inline child workflow as one opaque node via the
same ``flow.executor.execute()``.  Its ports are derived from the child's signature
(not declared).  Both engines call the same ``execute()`` on the child, so child
semantics are byte-identical by construction.  See ``docs/subflow.md``.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import types
from typing import Any

from flow.codegen import generate
from flow.executor import execute
from flow.loader import parse_workflow


def _child() -> dict[str, Any]:
    """A script-only child: uppercases topic -> verdict (no LLM)."""
    return {
        "name": "up",
        "provider": "copilot",
        "entry": "u",
        "state": {"topic": "x"},
        "nodes": [
            {
                "id": "u",
                "type": "script",
                "inputs": [{"name": "topic", "type": "string"}],
                "code": "def u(ctx, topic):\n    return topic.upper()",
                "outputs": ["verdict"],
            }
        ],
        "edges": [
            {"from": "$in", "to": "u", "map": {"topic": "topic"}},
            {"from": "u", "to": "$output", "map": {"verdict": "verdict"}},
        ],
    }


def _parent(seed: str = "hello") -> dict[str, Any]:
    """plan -> subflow(child) -> pub, threading a seed string through."""
    return {
        "name": "main",
        "provider": "copilot",
        "entry": "plan",
        "state": {"seed": seed},
        "nodes": [
            {
                "id": "plan",
                "type": "script",
                "inputs": [{"name": "seed", "type": "string"}],
                "code": "def p(ctx, seed):\n    return seed + '!'",
                "outputs": ["topic"],
            },
            {"id": "review", "type": "subflow", "subflow": _child()},
            {
                "id": "pub",
                "type": "script",
                "inputs": [{"name": "verdict", "type": "string"}],
                "code": "def pub(ctx, verdict):\n    return '[' + verdict + ']'",
                "outputs": ["done"],
            },
        ],
        "edges": [
            {"from": "$in", "to": "plan", "map": {"seed": "seed"}},
            {"from": "plan", "to": "review", "map": {"topic": "topic"}},
            {"from": "review", "to": "pub", "map": {"verdict": "verdict"}},
            {"from": "pub", "to": "$output", "map": {"done": "result"}},
        ],
    }


# --- interpreter behaviour -------------------------------------------------


async def test_subflow_interpreter_e2e() -> None:
    wf = parse_workflow(_parent("hello"))
    r = await execute(wf, stream_fn_factory=lambda m: None)
    # hello -> plan 'hello!' -> child uppercases 'HELLO!' -> pub '[HELLO!]'
    assert r.runtime["state"]["review"] == {"verdict": "HELLO!"}
    assert r.runtime["out"]["result"] == "[HELLO!]"


async def test_subflow_is_one_scheduler_node() -> None:
    """The subflow is ONE node: one completed id, one 'review' trace frame."""
    wf = parse_workflow(_parent("hi"))
    r = await execute(wf, stream_fn_factory=lambda m: None)
    review_frames = [f for f in r.runtime["stack"] if f["node"] == "review"]
    assert len(review_frames) == 1


# --- cross-engine parity (interpret == compile) ----------------------------


async def test_subflow_interpret_equals_compile() -> None:
    wf = parse_workflow(_parent("world"))
    interp = await execute(wf, stream_fn_factory=lambda m: None)

    src = generate(wf)
    compile(src, "<gen>", "exec")  # compiles

    mod = types.ModuleType("_gen_sub")
    exec(compile(src, "<gen_sub>", "exec"), mod.__dict__)  # noqa: S102
    await mod.main()  # type: ignore[attr-defined]
    gen_runtime: dict[str, Any] = mod._RUNTIME  # type: ignore[attr-defined]

    assert gen_runtime["state"] == dict(interp.runtime["state"])
    assert gen_runtime["out"] == dict(interp.runtime["out"])


def test_subflow_generated_module_imports_flow() -> None:
    """A subflow-using module imports flow (the accepted trade); it embeds the child."""
    src = generate(parse_workflow(_parent()))
    assert "from flow.executor import execute" in src
    assert "_CHILD_review = {" in src


def test_non_subflow_module_stays_flow_independent() -> None:
    """A workflow with no subflow node must NOT import flow (regression guard)."""
    d = {
        "name": "plain",
        "provider": "copilot",
        "entry": "a",
        "state": {"x": "hi"},
        "nodes": [{"id": "a", "type": "script", "inputs": ["x"],
                   "code": "def a(ctx, x):\n    return x", "outputs": ["y"]}],
        "edges": [{"from": "$in", "to": "a", "map": {"x": "x"}},
                  {"from": "a", "to": "$output", "map": {"y": "result"}}],
    }
    src = generate(parse_workflow(d))
    assert "import flow" not in src
    assert "from flow" not in src


def test_subflow_generated_module_is_ruff_clean() -> None:
    src = generate(parse_workflow(_parent()))
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
        tf.write(src)
        tmp = pathlib.Path(tf.name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--line-length", "120", str(tmp)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ruff failed:\n{result.stdout}\n{result.stderr}"
    finally:
        tmp.unlink(missing_ok=True)
