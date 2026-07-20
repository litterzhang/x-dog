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
from flow.runtime import RuntimeContext

_EXAMPLES_DIR = pathlib.Path(__file__).parent.parent / "examples"
_WF_PATH = _EXAMPLES_DIR / "codegen_builder.json"


def _ctx(**inputs: str) -> RuntimeContext:
    """A minimal RuntimeContext for calling script functions directly."""
    return RuntimeContext(inputs=inputs, workflow_name="test", node_id="n")


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
    out = json.loads(await next_task(_ctx(), tasks=json.dumps(["a", "b", "c"])))
    assert out == {"current": "a", "remaining": ["b", "c"], "done": False}


async def test_next_task_empty_is_done() -> None:
    out = json.loads(await next_task(_ctx(), tasks="[]"))
    assert out == {"current": "", "remaining": [], "done": True}


async def test_next_task_plain_string() -> None:
    out = json.loads(await next_task(_ctx(), tasks="single task"))
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
    report = await run_checks(_ctx(), target_path=str(tmp_path))
    assert report.startswith("PASS"), report


async def test_run_checks_fail_ruff(tmp_path: pathlib.Path) -> None:
    # Unused import -> ruff F401 failure.
    (tmp_path / "bad.py").write_text("import os\n", encoding="utf-8")
    report = await run_checks(_ctx(), target_path=str(tmp_path))
    assert report.startswith("FAIL"), report


async def test_run_checks_no_target() -> None:
    report = await run_checks(_ctx(), target_path="")
    assert report.startswith("FAIL")


# ---------------------------------------------------------------------------
# verify_generated_module + autofix_module (production codegen gate)
# ---------------------------------------------------------------------------


async def test_verify_generated_module_needs_all_paths() -> None:
    report = await verify_generated_module(_ctx(), module_file="x", mypy_target="", test_file="")
    assert report.startswith("FAIL")


async def test_autofix_module_cleans_imports(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "mod.py"
    # unused import + unsorted -> ruff --fix removes/reorders it
    f.write_text("import sys\nimport os\n\n\ndef g() -> int:\n    return 1\n", encoding="utf-8")
    report = await autofix_module(_ctx(), module_file=str(f))
    assert "autofixed" in report
    cleaned = f.read_text(encoding="utf-8")
    assert "import os" not in cleaned
    assert "import sys" not in cleaned


async def test_autofix_module_no_target_is_safe() -> None:
    report = await autofix_module(_ctx(), module_file="")
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
    def _factory(model: str) -> Any:
        # After an agent submits its result, the agent loop calls the stream fn
        # again to observe the tool result; that follow-up must return a plain
        # `stop` so the turn ends (otherwise the agent loops forever).
        submitted: dict[str, bool] = {"designer": False, "reviewer": False}

        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> "AiEventStream[AssistantMessage]":
            stream: AiEventStream[AssistantMessage] = AiEventStream()
            sp = _system_prompt_of(context).lower()

            if "designer" in sp and not submitted["designer"]:
                submitted["designer"] = True
                payload = {"module_path": "add.py", "test_path": "test_add.py", "summary": "add(a,b)"}
                msg = AssistantMessage(
                    content=(ToolCall(id="d1", name="submit_result", arguments={"result": payload}),),
                    stop_reason="toolUse",
                )
            elif "reviewer" in sp and not submitted["reviewer"]:
                submitted["reviewer"] = True
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
    """The whole 6-node pipeline completes under a fake stream, no provider needed.

    Script nodes are stubbed via ``script_resolver`` so no real ruff/mypy/pytest
    subprocess runs; ``verify`` returns PASS so the review->implement loop stays
    quiet and the run is fast + deterministic.
    """
    wf = load_workflow(_WF_PATH)
    factory = _make_pipeline_fake()

    async def _intake(ctx: Any, **kw: Any) -> str:
        return json.dumps({"current": "add(a,b)", "remaining": [], "done": False})

    async def _verify(ctx: Any, **kw: Any) -> str:
        return "PASS: all checks clean"

    async def _autofix(ctx: Any, **kw: Any) -> str:
        return "autofixed (stub)"

    def _resolver(run: str) -> Any:
        if run.endswith(":next_task") or run.endswith(":summarize_spec"):
            return _intake
        if run.endswith(":verify_generated_module") or run.endswith(":run_checks"):
            return _verify
        return _autofix

    result = await execute(
        wf,
        stream_fn_factory=factory,
        tool_registry=registry_with_filesystem(),
        script_resolver=_resolver,
    )

    out = result.outputs
    # intake (script) produced the task JSON in its 'task' port
    assert "task" in out["intake"]
    assert json.loads(out["intake"]["task"])["current"]
    # setup + implement agent nodes ran
    assert "setup_report" in out["setup"]
    assert "impl_report" in out["implement"]
    # design + review produced structured JSON via submit_result
    assert "plan" in out["design"]
    plan = json.loads(out["design"]["plan"])
    assert plan["module_path"] == "add.py"
    assert "review_result" in out["review"]
    review = json.loads(out["review"]["review_result"])
    assert review["status"] == "approved"
