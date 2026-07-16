"""Integration test: load examples/research_write_review.json and execute dry-run."""

from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
import sys
import tempfile
import types
from typing import Any

from ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from ai.utils.event_stream import EventStream as AiEventStream
from flow.codegen import generate
from flow.executor import execute
from flow.loader import load_workflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = pathlib.Path(__file__).parent.parent / "examples"


def _make_stub_factory(responses: dict[str, str], default: str = "") -> Any:
    """Return a stream_fn_factory keyed by *model* that returns deterministic text."""

    def _factory(model: str) -> Any:
        text = responses.get(model, default)

        def _stream_fn(
            model_id: Any,
            context: Any,
            options: Any = None,
        ) -> AiEventStream[AssistantMessage]:
            msg = AssistantMessage(content=(TextContent(text=text),))
            stream: AiEventStream[AssistantMessage] = AiEventStream()

            async def _push() -> None:
                await asyncio.sleep(0)
                await stream.send(DoneEvent(stop_reason="stop", message=msg))
                stream.set_result(msg)
                await stream.close()

            asyncio.ensure_future(_push())
            return stream

        return _stream_fn

    return _factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_dryrun() -> None:
    """Load research_write_review.json and execute with stub stream functions.

    The review node returns 'APPROVED' so the conditional back-edge is never
    taken.  We verify that the executor completes and final_state contains the
    expected output keys: research_notes (findings), article (draft), and
    review_result (verdict).
    """
    wf_path = _EXAMPLES_DIR / "research_write_review.json"
    wf = load_workflow(wf_path)

    # All nodes share the same model (claude-sonnet-4.5 from the JSON defaults).
    # Use a single default response for research + write nodes; override the
    # review node's model to return APPROVED so the loop is not triggered.
    model = wf.default_model  # "claude-sonnet-4.5"
    stub_factory = _make_stub_factory(
        responses={model: "APPROVED"},
        default="APPROVED",
    )

    result = await execute(wf, stream_fn_factory=stub_factory)

    # All three output keys must be present in final_state
    assert "research_notes" in result.final_state, "missing research_notes"
    assert "article" in result.final_state, "missing article"
    assert "review_result" in result.final_state, "missing review_result"

    # The review node must have returned APPROVED (loop not taken)
    assert "APPROVED" in result.final_state["review_result"]

    # node_outputs mirrors final_state for these keys
    assert result.node_outputs["research_notes"] == result.final_state["research_notes"]
    assert result.node_outputs["article"] == result.final_state["article"]
    assert result.node_outputs["review_result"] == result.final_state["review_result"]


async def test_generate_parity() -> None:
    """Generate the example workflow; ruff-check it; exec it with a stub provider.

    Structural assertions:
    - generated module compiles
    - ruff check passes
    - defines one async node function per workflow node plus async main()

    State parity assertion:
    - run the interpreter (execute()) with the same stub and verify final_state
      matches the expected keys and values (mirrors test_run_dryrun)
    """
    wf_path = _EXAMPLES_DIR / "research_write_review.json"
    wf = load_workflow(wf_path)

    src = generate(wf)

    # --- structural: compiles ---
    compile(src, "<generated>", "exec")

    # --- structural: ruff-clean ---
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
        tf.write(src)
        tmp_path = pathlib.Path(tf.name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--line-length", "120", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ruff failed:\n{result.stdout}\n{result.stderr}"
    finally:
        tmp_path.unlink(missing_ok=True)

    # --- structural: node functions and main() present ---
    for node in wf.nodes:
        fn_name = f"async def node_{node.id}"
        assert fn_name in src, f"missing node function: {fn_name}"
    assert "async def main()" in src

    # --- exec with patched ai / agent.helpers so no real LLM is called ---
    model = wf.default_model  # "claude-sonnet-4.5"
    stub_text = "APPROVED"

    def _stub_stream_fn(
        model_id: Any,
        context: Any,
        options: Any = None,
    ) -> AiEventStream[AssistantMessage]:
        msg = AssistantMessage(content=(TextContent(text=stub_text),))
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason="stop", message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    # Patch agent.helpers.stream_fn_from_provider to return _stub_stream_fn
    # regardless of the provider object passed, so the generated module never
    # hits a real LLM.
    import agent.helpers as _agent_helpers
    import ai as _ai

    original_sfp = _agent_helpers.stream_fn_from_provider
    _agent_helpers.stream_fn_from_provider = lambda _provider: _stub_stream_fn  # type: ignore[assignment]

    # Patch ai.provider to return a sentinel object (won't be used beyond sfp).
    _sentinel_provider = object()
    original_ai_provider = _ai.provider
    _ai.provider = lambda _name: _sentinel_provider  # type: ignore[assignment]

    # exec the generated source in a fresh module namespace; because we've
    # patched the already-loaded modules in sys.modules, the `from agent.helpers
    # import stream_fn_from_provider` inside the compiled code picks up the stub.
    gen_module = types.ModuleType("_generated_workflow")

    try:
        exec(compile(src, "<generated_workflow>", "exec"), gen_module.__dict__)  # noqa: S102

        # Call main() on the generated module.
        await gen_module.main()  # type: ignore[attr-defined]

        # Verify STATE in the generated module.
        gen_state: dict[str, str] = gen_module.STATE  # type: ignore[attr-defined]

        assert "research_notes" in gen_state, f"generated STATE missing research_notes: {gen_state}"
        assert "article" in gen_state, f"generated STATE missing article: {gen_state}"
        assert "review_result" in gen_state, f"generated STATE missing review_result: {gen_state}"
        assert stub_text in gen_state["review_result"], (
            f"expected '{stub_text}' in review_result, got: {gen_state['review_result']}"
        )

        # --- state parity: interpreter result must match generated module result ---
        stub_factory = _make_stub_factory(responses={model: stub_text}, default=stub_text)
        interp_result = await execute(wf, stream_fn_factory=stub_factory)

        assert gen_state["research_notes"] == interp_result.final_state["research_notes"]
        assert gen_state["article"] == interp_result.final_state["article"]
        assert gen_state["review_result"] == interp_result.final_state["review_result"]

    finally:
        _agent_helpers.stream_fn_from_provider = original_sfp  # type: ignore[assignment]
        _ai.provider = original_ai_provider  # type: ignore[assignment]


async def test_tools_script_dryrun() -> None:
    """Load examples/tools_script.json; execute with dry-run factory + default registry.

    The script node (prep) runs its inline ctx-first function for real, copying
    state['topic'] -> state['prepped'].  The agent node (analyze) uses the
    stub stream_fn and must produce some output stored in state['analysis'].
    """
    wf_path = _EXAMPLES_DIR / "tools_script.json"
    wf = load_workflow(wf_path)

    def _dryrun_factory(m: str) -> Any:
        dryrun_text = f"DRYRUN:{m}"

        def _stream_fn(
            model_id: Any,
            context: Any,
            options: Any = None,
        ) -> AiEventStream[AssistantMessage]:
            msg = AssistantMessage(content=(TextContent(text=dryrun_text),))
            stream: AiEventStream[AssistantMessage] = AiEventStream()

            async def _push() -> None:
                await asyncio.sleep(0)
                await stream.send(DoneEvent(stop_reason="stop", message=msg))
                stream.set_result(msg)
                await stream.close()

            asyncio.ensure_future(_push())
            return stream

        return _stream_fn

    from flow.tools import default_registry

    result = await execute(wf, stream_fn_factory=_dryrun_factory, tool_registry=default_registry())

    # Script node must have run its inline function and stored the topic value
    assert result.final_state.get("prepped") == "workflow engines"

    # Agent node must have stored something under 'analysis'
    assert "analysis" in result.final_state


async def test_generate_tools_script_parity() -> None:
    """Generate tools_script.json; ruff-check; compile; assert structural properties."""
    wf_path = _EXAMPLES_DIR / "tools_script.json"
    wf = load_workflow(wf_path)

    src = generate(wf)

    # --- structural: compiles ---
    compile(src, "<generated>", "exec")

    # --- structural: ruff-clean ---
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
        tf.write(src)
        tmp_path = pathlib.Path(tf.name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--line-length", "120", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ruff failed:\n{result.stdout}\n{result.stderr}"
    finally:
        tmp_path.unlink(missing_ok=True)

    # --- structural: inline script node emits a self-contained ctx-first
    # function + wrapper; agent node uses _REGISTRY.resolve ---
    assert "def _script_prep(ctx" in src, "inline script must be emitted as a top-level _script_prep"
    assert "_script_prep(_ctx)" in src, "generated wrapper must call _script_prep with a RuntimeContext"
    assert "RuntimeContext(" in src, "script wrapper must build a RuntimeContext"
    assert "_REGISTRY.resolve(" in src, "generated agent node must call _REGISTRY.resolve("


# ---------------------------------------------------------------------------
# Step 25: auto_enrich example — declared inputs + structured output
# ---------------------------------------------------------------------------

_ENRICH_RESULT = {"category": "IoT / Wireless", "token": "HNT", "summary": "Decentralized wireless network"}


def _make_submit_result_factory_integration(result_obj: dict[str, Any]) -> Any:
    """Stream factory whose agent emits submit_result then stops (for integration test)."""

    def _factory(model: str) -> Any:
        call_count: list[int] = [0]

        def _stream_fn(
            model_id: Any,
            context: Any,
            options: Any = None,
        ) -> AiEventStream[AssistantMessage]:
            stream: AiEventStream[AssistantMessage] = AiEventStream()

            if call_count[0] == 0:
                call_count[0] += 1
                tool_call = ToolCall(id="tc-enrich-1", name="submit_result", arguments={"result": result_obj})
                msg = AssistantMessage(content=(tool_call,), stop_reason="toolUse")
            else:
                msg = AssistantMessage(content=(TextContent(text="done"),), stop_reason="stop")

            async def _push() -> None:
                await asyncio.sleep(0)
                await stream.send(DoneEvent(stop_reason=msg.stop_reason, message=msg))
                stream.set_result(msg)
                await stream.close()

            asyncio.ensure_future(_push())
            return stream

        return _stream_fn

    return _factory


async def test_auto_enrich_dryrun() -> None:
    """Load examples/auto_enrich.json; execute with a fake stream that drives submit_result.

    Assertions:
    - validate passes (no exception from load_workflow)
    - final_state['record'] equals the initial topic (the inline pull script copies state['topic'])
    - final_state['enriched'] is present and json.loads() yields category/token/summary keys
    - final_state['saved'] is present
    """
    wf_path = _EXAMPLES_DIR / "auto_enrich.json"
    wf = load_workflow(wf_path)  # raises on validation failure

    topic = dict(wf.initial_state).get("topic", "")

    factory = _make_submit_result_factory_integration(_ENRICH_RESULT)

    from flow.tools import default_registry

    result = await execute(wf, stream_fn_factory=factory, tool_registry=default_registry())

    # pull node: the inline script copies state['topic'] into state['record']
    assert result.final_state.get("record") == topic, (
        f"expected record={topic!r}, got {result.final_state.get('record')!r}"
    )

    # enrich node: structured output stored as JSON
    assert "enriched" in result.final_state, "missing enriched key"
    enriched = json.loads(result.final_state["enriched"])
    assert "category" in enriched, f"enriched missing 'category': {enriched}"
    assert "token" in enriched, f"enriched missing 'token': {enriched}"
    assert "summary" in enriched, f"enriched missing 'summary': {enriched}"

    # persist node: the inline script echoes state['record'] into state['saved']
    assert "saved" in result.final_state, "missing saved key"
    assert result.final_state["saved"] == topic
