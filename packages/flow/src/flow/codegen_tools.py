"""flow.codegen_tools — script-node functions + tool registry for the codegen demo.

These back the ``examples/codegen_builder.json`` workflow, which demonstrates
that ``flow`` can orchestrate a code-generation pipeline:
``design -> implement (writes a file) -> verify (ruff/pytest) -> review (loop)``.

Everything here is flow-only (no agent-package edits).  Script nodes have the
signature ``async def fn(state: Mapping[str, str]) -> str`` and their return
value is stored under the node's ``output`` key.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
from collections.abc import Mapping

from agent.tools import create_bash_tool, create_filesystem_tool

from flow.tools import ToolRegistry, default_registry

# Cap each verification subprocess so a runaway check can't hang the workflow.
_CHECK_TIMEOUT_S = 120.0
# Keep only the tail of tool output in the state report (state values are strings).
_REPORT_TAIL = 4000


async def summarize_spec(state: Mapping[str, str]) -> str:
    """Entry script node: surface the spec into the pipeline state.

    Mirrors :func:`flow.tools.passthrough`; returns ``state['spec']`` (or empty).
    """
    return state.get("spec", "")


async def next_task(state: Mapping[str, str]) -> str:
    """Task-queue script node: pop the next pending task from ``state['tasks']``.

    ``state['tasks']`` is a JSON array of task strings (a plain string is treated
    as a one-item list).  Returns a JSON object string::

        {"current": "<task>", "remaining": ["..."], "done": false}

    When the queue is empty it returns ``{"current": "", "remaining": [],
    "done": true}`` so the workflow can branch to completion.  Deterministic;
    no LLM involved.
    """
    raw = state.get("tasks", "[]")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = [raw] if raw else []
    if isinstance(parsed, str):
        parsed = [parsed]
    tasks = [str(t) for t in parsed] if isinstance(parsed, list) else []

    if not tasks:
        return json.dumps({"current": "", "remaining": [], "done": True}, ensure_ascii=False)
    current, *remaining = tasks
    return json.dumps(
        {"current": current, "remaining": remaining, "done": False},
        ensure_ascii=False,
    )


async def _run(cmd: list[str], cwd: str | None) -> tuple[int, str]:
    """Run *cmd* capturing combined output; return (returncode, text)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 1, f"failed to launch {shlex.join(cmd)}: {exc}"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_CHECK_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, f"timeout after {_CHECK_TIMEOUT_S:.0f}s: {shlex.join(cmd)}"
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace")


async def run_checks(state: Mapping[str, str]) -> str:
    """Verify script node: run ruff + pytest against ``state['target_path']``.

    Returns a compact report string beginning with ``PASS`` when both checks
    succeed, or ``FAIL:`` followed by the failing tool's output tail otherwise.
    The review node branches on the ``FAIL`` marker to drive the bounded loop.
    """
    target = state.get("target_path", "")
    if not target:
        return "FAIL: no target_path in state"

    ruff_rc, ruff_out = await _run([sys.executable, "-m", "ruff", "check", target], cwd=None)
    if ruff_rc != 0:
        return f"FAIL: ruff\n{ruff_out[-_REPORT_TAIL:]}"

    pytest_rc, pytest_out = await _run([sys.executable, "-m", "pytest", "-q", target], cwd=None)
    if pytest_rc != 0:
        return f"FAIL: pytest\n{pytest_out[-_REPORT_TAIL:]}"

    return "PASS: ruff + pytest clean"


async def verify_generated_module(state: Mapping[str, str]) -> str:
    """Production gate for a generated source file.

    Runs, in order, against paths taken from state:
      1. ``ruff check`` on ``state['module_file']``
      2. ``mypy --strict`` on ``state['mypy_target']``  (a src dir/file)
      3. ``pytest`` on ``state['test_file']``            (the acceptance contract)

    Returns ``PASS`` only if all three succeed, else ``FAIL: <stage>`` with the
    failing output tail so the review node can drive the regeneration loop.  This
    is the gate that makes flow codegen production-grade: a generated module must
    lint, type-check under --strict, and satisfy its contract test.
    """
    module_file = state.get("module_file", "")
    mypy_target = state.get("mypy_target", "")
    test_file = state.get("test_file", "")
    if not module_file or not mypy_target or not test_file:
        return "FAIL: need module_file, mypy_target, and test_file in state"

    ruff_rc, ruff_out = await _run([sys.executable, "-m", "ruff", "check", module_file], cwd=None)
    if ruff_rc != 0:
        return f"FAIL: ruff\n{ruff_out[-_REPORT_TAIL:]}"

    mypy_rc, mypy_out = await _run([sys.executable, "-m", "mypy", "--strict", mypy_target], cwd=None)
    if mypy_rc != 0:
        return f"FAIL: mypy\n{mypy_out[-_REPORT_TAIL:]}"

    pytest_rc, pytest_out = await _run([sys.executable, "-m", "pytest", "-q", test_file], cwd=None)
    if pytest_rc != 0:
        return f"FAIL: pytest\n{pytest_out[-_REPORT_TAIL:]}"

    return "PASS: ruff + mypy --strict + pytest clean"


def registry_with_filesystem() -> ToolRegistry:
    """Return a registry with the built-in demo tools plus real agent tools.

    Adds the ``filesystem`` tool (so an agent node can write files) and the
    ``bash`` tool (so a setup node can create a git worktree for isolation).
    Pass this to :func:`flow.executor.execute` as ``tool_registry=`` so agent
    nodes declaring ``"tools": ["filesystem"]`` or ``["bash"]`` resolve.
    """
    registry = default_registry()
    registry.register(create_filesystem_tool())
    registry.register(create_bash_tool())
    return registry
