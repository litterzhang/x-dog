"""Integration test: load examples/research_write_review.json and execute dry-run."""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

from ai.types import AssistantMessage, DoneEvent, TextContent
from ai.utils.event_stream import EventStream as AiEventStream
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
