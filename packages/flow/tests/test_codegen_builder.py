"""Dry-run + unit tests for the codegen-builder demo workflow (no LLM, no network).

Covers:
- the workflow validates and renders a graph with the bounded review->implement loop;
- the ``run_checks`` script node reports PASS/FAIL against a real temp dir;
- ``next_task`` pops the task queue deterministically;
- ``registry_with_filesystem`` resolves the filesystem + bash tools;
- the whole pipeline executes end-to-end under a fake stream (schema nodes are
  driven via submit_result; plain agent nodes return text) with NO provider.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

from ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from ai.utils.event_stream import EventStream as AiEventStream
from flow.codegen_tools import (
    autofix_module,
    next_task,
    registry_with_filesystem,
    run_checks,
    verify_generated_module,
)
from flow.executor import execute
from flow.loader import load_workflow

_EXAMPLES_DIR = pathlib.Path(__file__).parent.parent / "examples"
_WF_PATH = _EXAMPLES_DIR / "codegen_builder.json"


# ---------------------------------------------------------------------------
# validate + graph
# ---------------------------------------------------------------------------


def test_validate_and_graph() -> None:
    from flow.graph import to_mermaid

    wf = load_workflow(_WF_PATH)  # raises on validation failure
    mermaid = to_mermaid(wf)
    for node_id in ("intake", "setup", "design", "implement", "verify", "review"):
        assert node_id in mermaid, f"missing node {node_id} in graph"
    # bounded loop edge review -> implement
    assert "review -- contains:FAIL --> implement" in mermaid


# ---------------------------------------------------------------------------
# next_task (task queue script node)
# ---------------------------------------------------------------------------


async def test_next_task_pops_head() -> None:
    out = json.loads(await next_task({"tasks": json.dumps(["a", "b", "c"])}))
    assert out == {"current": "a", "remaining": ["b", "c"], "done": False}


async def test_next_task_empty_is_done() -> None:
    out = json.loads(await next_task({"tasks": "[]"}))
    assert out == {"current": "", "remaining": [], "done": True}


async def test_next_task_plain_string() -> None:
    out = json.loads(await next_task({"tasks": "single task"}))
    assert out["current"] == "single task"
    assert out["done"] is False


# ---------------------------------------------------------------------------
# run_checks (verify script node) against real temp files
# ---------------------------------------------------------------------------


async def test_run_checks_pass(tmp_path: pathlib.Path) -> None:
    (tmp_path / "mod.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_mod.py").write_text(
        "from mod import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    report = await run_checks({"target_path": str(tmp_path)})
    assert report.startswith("PASS"), report


async def test_run_checks_fail_ruff(tmp_path: pathlib.Path) -> None:
    # Unused import -> ruff F401 failure.
    (tmp_path / "bad.py").write_text("import os\n", encoding="utf-8")
    report = await run_checks({"target_path": str(tmp_path)})
    assert report.startswith("FAIL"), report


async def test_run_checks_no_target() -> None:
    report = await run_checks({})
    assert report.startswith("FAIL")


# ---------------------------------------------------------------------------
# verify_generated_module + autofix_module (production codegen gate)
# ---------------------------------------------------------------------------


async def test_verify_generated_module_needs_all_paths() -> None:
    report = await verify_generated_module({"module_file": "x"})
    assert report.startswith("FAIL")


async def test_autofix_module_cleans_imports(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "mod.py"
    # unused import + unsorted -> ruff --fix removes/reorders it
    f.write_text("import sys\nimport os\n\n\ndef g() -> int:\n    return 1\n", encoding="utf-8")
    report = await autofix_module({"module_file": str(f)})
    assert "autofixed" in report
    cleaned = f.read_text(encoding="utf-8")
    assert "import os" not in cleaned
    assert "import sys" not in cleaned


async def test_autofix_module_no_target_is_safe() -> None:
    report = await autofix_module({})
    assert "skipped" in report


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_registry_resolves_filesystem_and_bash() -> None:
    reg = registry_with_filesystem()
    tools = reg.resolve(["filesystem", "bash"])
    names = {t.name for t in tools}
    assert "filesystem" in names
    assert "bash" in names


# ---------------------------------------------------------------------------
# full pipeline dry-run (fake stream, no network)
# ---------------------------------------------------------------------------


def _system_prompt_of(context: Any) -> str:
    sp = getattr(context, "system_prompt", "")
    return sp if isinstance(sp, str) else str(sp)


def _push(stream: "AiEventStream[AssistantMessage]", msg: AssistantMessage) -> None:
    async def _run() -> None:
        await asyncio.sleep(0)
        await stream.send(DoneEvent(stop_reason=msg.stop_reason, message=msg))
        stream.set_result(msg)
        await stream.close()

    asyncio.ensure_future(_run())


def _make_pipeline_fake() -> Any:
    """Fake stream_fn_factory that answers each agent node by inspecting its system prompt.

    - design node (mentions 'designer') -> submit_result with module/test paths
    - review node (mentions 'reviewer')  -> submit_result with status 'approved'
    - other agent nodes (setup, implement) -> plain text (no tool call needed to
      complete the turn in dry-run; real tool use is exercised in the live run)
    """
    submitted: dict[str, int] = {"design": 0, "review": 0}

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> "AiEventStream[AssistantMessage]":
            stream: AiEventStream[AssistantMessage] = AiEventStream()
            sp = _system_prompt_of(context).lower()

            if "designer" in sp and submitted["design"] == 0:
                submitted["design"] = 1
                payload = {"module_path": "add.py", "test_path": "test_add.py", "summary": "add(a,b)"}
                msg = AssistantMessage(
                    content=(ToolCall(id="d1", name="submit_result", arguments={"result": payload}),),
                    stop_reason="toolUse",
                )
            elif "reviewer" in sp and submitted["review"] == 0:
                submitted["review"] = 1
                payload = {"status": "approved", "feedback": "looks good"}
                msg = AssistantMessage(
                    content=(ToolCall(id="r1", name="submit_result", arguments={"result": payload}),),
                    stop_reason="toolUse",
                )
            else:
                msg = AssistantMessage(content=(TextContent(text="done"),), stop_reason="stop")

            _push(stream, msg)
            return stream

        return _stream_fn

    return _factory


async def test_pipeline_dryrun() -> None:
    """The whole 6-node pipeline completes under a fake stream, no provider needed."""
    wf = load_workflow(_WF_PATH)
    factory = _make_pipeline_fake()

    result = await execute(wf, stream_fn_factory=factory, tool_registry=registry_with_filesystem())

    fs = result.final_state
    # intake (script) produced the task JSON
    assert "task" in fs
    assert json.loads(fs["task"])["current"]
    # setup + implement agent nodes ran
    assert "setup_report" in fs
    assert "impl_report" in fs
    # design + review produced structured JSON via submit_result
    assert "plan" in fs
    plan = json.loads(fs["plan"])
    assert plan["module_path"] == "add.py"
    assert "review_result" in fs
    review = json.loads(fs["review_result"])
    assert review["status"] == "approved"
