"""Tests for flow.executor — linear workflow execution."""

from __future__ import annotations

import asyncio
from typing import Any

from ai.types import AssistantMessage, DoneEvent, TextContent
from ai.utils.event_stream import EventStream as AiEventStream
from flow.executor import ExecResult, execute
from flow.models import EdgeDef, NodeDef, WorkflowDef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL = "UPSTREAM_RESULT_XYZ"


def _make_stream_fn(responses: dict[str, str]) -> Any:
    """Return a stream_fn_factory that maps model -> deterministic response text.

    ``responses`` maps model-id strings to the text the fake LLM will return.
    If the model is not in the map, returns an empty response.
    """

    def _factory(model: str) -> Any:
        text = responses.get(model, "")

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


def _make_prompt_capturing_factory(
    captured: list[tuple[str, str]],
) -> Any:
    """Factory that records (model, user_prompt) pairs and returns fixed text."""

    def _factory(model: str) -> Any:
        def _stream_fn(
            model_id: Any,
            context: Any,
            options: Any = None,
        ) -> AiEventStream[AssistantMessage]:
            # Extract the user prompt text from the context messages
            user_text = ""
            for msg in context.messages:
                if hasattr(msg, "content"):
                    for part in msg.content:
                        if hasattr(part, "text"):
                            user_text += part.text

            captured.append((model, user_text))
            response_text = f"response_from_{model}"
            msg = AssistantMessage(content=(TextContent(text=response_text),))
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


async def test_linear_two_nodes() -> None:
    """Two-node linear workflow: node1 output -> node2 sees it in prompt."""
    wf = WorkflowDef(
        name="test",
        provider="fake",
        entry="node1",
        default_model="model-a",
        nodes=(
            NodeDef(id="node1", model="model-a", prompt="task1", output="step1_out"),
            NodeDef(id="node2", model="model-b", prompt="task2 uses {{step1_out}}", output="step2_out"),
        ),
        edges=(EdgeDef(src="node1", dst="node2"),),
    )

    factory = _make_stream_fn({"model-a": "result_from_a", "model-b": "result_from_b"})
    result = await execute(wf, stream_fn_factory=factory)

    assert isinstance(result, ExecResult)
    assert result.final_state["step1_out"] == "result_from_a"
    assert result.final_state["step2_out"] == "result_from_b"
    assert result.node_outputs["step1_out"] == "result_from_a"
    assert result.node_outputs["step2_out"] == "result_from_b"


async def test_linear_sentinel_threading() -> None:
    """Verify upstream output is interpolated into downstream prompt."""
    captured: list[tuple[str, str]] = []
    factory = _make_prompt_capturing_factory(captured)

    wf = WorkflowDef(
        name="sentinel",
        provider="fake",
        entry="node1",
        default_model="m",
        nodes=(
            NodeDef(id="node1", model="m", prompt="first prompt", output="out1"),
            NodeDef(
                id="node2",
                model="m",
                prompt=f"second prompt uses {_SENTINEL}: {{{{out1}}}}",
                output="out2",
            ),
        ),
        edges=(EdgeDef(src="node1", dst="node2"),),
    )

    result = await execute(wf, stream_fn_factory=factory)

    # node2's prompt must contain the sentinel and the upstream output
    assert len(captured) == 2
    node2_prompt = captured[1][1]
    assert _SENTINEL in node2_prompt
    assert "response_from_m" in node2_prompt  # upstream output from node1

    # final state reflects both outputs
    assert "out1" in result.final_state
    assert "out2" in result.final_state


async def test_linear_three_nodes() -> None:
    """Three-node chain accumulates state across all nodes."""
    wf = WorkflowDef(
        name="chain3",
        provider="fake",
        entry="a",
        default_model="m",
        nodes=(
            NodeDef(id="a", model="m", prompt="step a", output="va"),
            NodeDef(id="b", model="m", prompt="step b after {{va}}", output="vb"),
            NodeDef(id="c", model="m", prompt="step c after {{va}} and {{vb}}", output="vc"),
        ),
        edges=(
            EdgeDef(src="a", dst="b"),
            EdgeDef(src="b", dst="c"),
        ),
    )

    factory = _make_stream_fn({"m": "ok"})
    result = await execute(wf, stream_fn_factory=factory)

    assert result.final_state["va"] == "ok"
    assert result.final_state["vb"] == "ok"
    assert result.final_state["vc"] == "ok"
    assert set(result.node_outputs.keys()) == {"va", "vb", "vc"}


async def test_no_output_node() -> None:
    """Node without output= does not pollute state."""
    wf = WorkflowDef(
        name="no_out",
        provider="fake",
        entry="only",
        default_model="m",
        nodes=(NodeDef(id="only", model="m", prompt="do something"),),
        edges=(),
    )
    factory = _make_stream_fn({"m": "ignored"})
    result = await execute(wf, stream_fn_factory=factory)

    assert result.final_state == {}
    assert result.node_outputs == {}


async def test_initial_state_available() -> None:
    """initial_state values are accessible in the first node prompt."""
    captured: list[tuple[str, str]] = []
    factory = _make_prompt_capturing_factory(captured)

    wf = WorkflowDef(
        name="init_state",
        provider="fake",
        entry="only",
        default_model="m",
        initial_state=(("input_var", "hello_initial"),),
        nodes=(NodeDef(id="only", model="m", prompt="use {{input_var}}", output="res"),),
        edges=(),
    )

    await execute(wf, stream_fn_factory=factory)

    assert len(captured) == 1
    assert "hello_initial" in captured[0][1]
