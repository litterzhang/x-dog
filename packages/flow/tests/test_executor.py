"""Tests for flow.executor — linear + parallel workflow execution.

Data flows through node-private ports: a node reads its input ports (fed by
incoming edge mappings) and writes its output ports.  ``result.outputs`` is
nested ``outputs[node_id][port]``; the reserved ``$in`` source holds the initial
state.  Prompt ``{{x}}`` is PORT-LOCAL (reads this node's input port ``x``).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent.agent import Agent
from ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from ai.utils.event_stream import EventStream as AiEventStream
from flow.errors import WorkflowExecutionError
from flow.executor import ExecResult, execute
from flow.models import IN_NODE_ID, Condition, EdgeDef, NodeDef, Port, WorkflowDef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL = "UPSTREAM_RESULT_XYZ"


def _make_stream_fn(responses: dict[str, str]) -> Any:
    """Return a stream_fn_factory that maps model -> deterministic response text."""

    def _factory(model: str) -> Any:
        text = responses.get(model, "")

        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
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


def _make_prompt_capturing_factory(captured: list[tuple[str, str]]) -> Any:
    """Factory that records (model, user_prompt) pairs and returns fixed text."""

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
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
    """Two-node linear workflow: node1 output -> node2 sees it in its prompt port."""
    wf = WorkflowDef(
        name="test",
        provider="fake",
        entry="node1",
        default_model="model-a",
        nodes=(
            NodeDef(id="node1", model="model-a", prompt="task1", output_ports=(Port("step1_out"),)),
            NodeDef(
                id="node2",
                model="model-b",
                prompt="task2 uses {{step1_out}}",
                input_ports=(Port("step1_out"),),
                output_ports=(Port("step2_out"),),
            ),
        ),
        edges=(EdgeDef(src="node1", dst="node2", mapping=(("step1_out", "step1_out"),)),),
    )

    factory = _make_stream_fn({"model-a": "result_from_a", "model-b": "result_from_b"})
    result = await execute(wf, stream_fn_factory=factory)

    assert isinstance(result, ExecResult)
    assert result.outputs["node1"]["step1_out"] == "result_from_a"
    assert result.outputs["node2"]["step2_out"] == "result_from_b"


async def test_linear_sentinel_threading() -> None:
    """Verify upstream output is interpolated into downstream prompt (port-local)."""
    captured: list[tuple[str, str]] = []
    factory = _make_prompt_capturing_factory(captured)

    wf = WorkflowDef(
        name="sentinel",
        provider="fake",
        entry="node1",
        default_model="m",
        nodes=(
            NodeDef(id="node1", model="m", prompt="first prompt", output_ports=(Port("out1"),)),
            NodeDef(
                id="node2",
                model="m",
                prompt=f"second prompt uses {_SENTINEL}: {{{{out1}}}}",
                input_ports=(Port("out1"),),
                output_ports=(Port("out2"),),
            ),
        ),
        edges=(EdgeDef(src="node1", dst="node2", mapping=(("out1", "out1"),)),),
    )

    result = await execute(wf, stream_fn_factory=factory)

    assert len(captured) == 2
    node2_prompt = captured[1][1]
    assert _SENTINEL in node2_prompt
    assert "response_from_m" in node2_prompt  # upstream output from node1

    assert "out1" in result.outputs["node1"]
    assert "out2" in result.outputs["node2"]


async def test_linear_three_nodes() -> None:
    """Three-node chain threads outputs through explicit port mappings."""
    wf = WorkflowDef(
        name="chain3",
        provider="fake",
        entry="a",
        default_model="m",
        nodes=(
            NodeDef(id="a", model="m", prompt="step a", output_ports=(Port("va"),)),
            NodeDef(
                id="b", model="m", prompt="step b after {{va}}", input_ports=(Port("va"),), output_ports=(Port("vb"),)
            ),
            NodeDef(
                id="c",
                model="m",
                prompt="step c after {{va}} and {{vb}}",
                input_ports=(Port("va"), Port("vb")),
                output_ports=(Port("vc"),),
            ),
        ),
        edges=(
            EdgeDef(src="a", dst="b", mapping=(("va", "va"),)),
            EdgeDef(src="a", dst="c", mapping=(("va", "va"),)),
            EdgeDef(src="b", dst="c", mapping=(("vb", "vb"),)),
        ),
    )

    factory = _make_stream_fn({"m": "ok"})
    result = await execute(wf, stream_fn_factory=factory)

    assert result.outputs["a"]["va"] == "ok"
    assert result.outputs["b"]["vb"] == "ok"
    assert result.outputs["c"]["vc"] == "ok"


async def test_no_output_node() -> None:
    """A node without output ports produces no output entry."""
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

    # Only the $in source is present; the node wrote no ports.
    assert result.outputs.get("only", {}) == {}


async def test_initial_state_available() -> None:
    """initial_state values reach the first node via a $in edge mapping."""
    captured: list[tuple[str, str]] = []
    factory = _make_prompt_capturing_factory(captured)

    wf = WorkflowDef(
        name="init_state",
        provider="fake",
        entry="only",
        default_model="m",
        initial_state=(("input_var", "hello_initial"),),
        nodes=(
            NodeDef(
                id="only",
                model="m",
                prompt="use {{input_var}}",
                input_ports=(Port("input_var"),),
                output_ports=(Port("res"),),
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="only", mapping=(("input_var", "input_var"),)),),
    )

    await execute(wf, stream_fn_factory=factory)

    assert len(captured) == 1
    assert "hello_initial" in captured[0][1]


async def test_parallel_diamond() -> None:
    """Diamond a -> (b, c) -> d: b and c run concurrently; d sees both outputs."""
    call_order: list[str] = []

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> Any:
            call_order.append(model)
            response_text = f"output_from_{model}"
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

    wf = WorkflowDef(
        name="diamond",
        provider="fake",
        entry="a",
        default_model="a",
        nodes=(
            NodeDef(id="a", model="a", prompt="step a", output_ports=(Port("va"),)),
            NodeDef(
                id="b", model="b", prompt="step b uses {{va}}", input_ports=(Port("va"),), output_ports=(Port("vb"),)
            ),
            NodeDef(
                id="c", model="c", prompt="step c uses {{va}}", input_ports=(Port("va"),), output_ports=(Port("vc"),)
            ),
            NodeDef(
                id="d",
                model="d",
                prompt="step d uses {{vb}} and {{vc}}",
                input_ports=(Port("vb"), Port("vc")),
                output_ports=(Port("vd"),),
            ),
        ),
        edges=(
            EdgeDef(src="a", dst="b", mapping=(("va", "va"),)),
            EdgeDef(src="a", dst="c", mapping=(("va", "va"),)),
            EdgeDef(src="b", dst="d", mapping=(("vb", "vb"),)),
            EdgeDef(src="c", dst="d", mapping=(("vc", "vc"),)),
        ),
    )

    result = await execute(wf, stream_fn_factory=_factory)

    assert result.outputs["b"]["vb"] == "output_from_b"
    assert result.outputs["c"]["vc"] == "output_from_c"
    assert result.outputs["d"]["vd"] == "output_from_d"

    assert call_order[0] == "a"
    assert call_order[-1] == "d"
    assert set(call_order[1:-1]) == {"b", "c"}


# ---------------------------------------------------------------------------
# Conditional edges + bounded loops
# ---------------------------------------------------------------------------


async def test_conditional_edge_not_taken() -> None:
    """An edge whose ``when`` condition is False is not traversed."""
    factory = _make_stream_fn({"m": "no_match"})

    wf = WorkflowDef(
        name="cond_skip",
        provider="fake",
        entry="a",
        default_model="m",
        nodes=(
            NodeDef(id="a", model="m", prompt="first", output_ports=(Port("va"),)),
            NodeDef(id="b", model="m", prompt="second", input_ports=(Port("va"),), output_ports=(Port("vb"),)),
        ),
        edges=(
            EdgeDef(
                src="a",
                dst="b",
                mapping=(("va", "va"),),
                when=Condition(op="contains", value="{{va}}", text="NEVER_PRESENT"),
            ),
        ),
    )

    result = await execute(wf, stream_fn_factory=factory)

    assert result.outputs["a"]["va"] == "no_match"
    assert "b" not in result.outputs


async def test_loop() -> None:
    """review->write back-edge with loop_max=2.

    The edge ``when`` reads the SOURCE node (review) output port ``verdict``.
    fake stream returns 'REVISE' then 'APPROVE'; write runs exactly twice.
    """
    write_run_count: list[int] = [0]
    review_responses = ["REVISE", "APPROVE"]
    review_call: list[int] = [0]

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            if model == "write-model":
                write_run_count[0] += 1
                text = "draft"
            else:
                idx = review_call[0]
                review_call[0] += 1
                text = review_responses[idx] if idx < len(review_responses) else "APPROVE"

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

    wf = WorkflowDef(
        name="review_loop",
        provider="fake",
        entry="write",
        default_model="write-model",
        nodes=(
            NodeDef(id="write", model="write-model", prompt="write something", output_ports=(Port("draft"),)),
            NodeDef(
                id="review",
                model="review-model",
                prompt="review {{draft}}",
                input_ports=(Port("draft"),),
                output_ports=(Port("verdict"),),
            ),
        ),
        edges=(
            EdgeDef(src="write", dst="review", mapping=(("draft", "draft"),)),
            EdgeDef(
                src="review",
                dst="write",
                when=Condition(op="contains", value="{{verdict}}", text="REVISE"),
                loop_max=2,
            ),
        ),
    )

    result = await execute(wf, stream_fn_factory=_factory)

    assert write_run_count[0] == 2
    assert result.outputs["review"]["verdict"] == "APPROVE"
    assert review_call[0] == 2


# ---------------------------------------------------------------------------
# Script nodes + agent tools
# ---------------------------------------------------------------------------


async def test_script_node() -> None:
    """Script node runs the resolved callable and stores output; stream_fn never called."""
    stream_fn_called: list[bool] = []

    def _never_factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> Any:
            stream_fn_called.append(True)
            raise AssertionError("stream_fn should not be called for script nodes")

        return _stream_fn

    async def _my_script(ctx: Any) -> str:
        return "SCRIPTOUT"

    wf = WorkflowDef(
        name="script_test",
        provider="fake",
        entry="s1",
        default_model="m",
        nodes=(NodeDef(id="s1", type="script", run="dummy:fn", output_ports=(Port("result"),)),),
        edges=(),
    )

    result = await execute(
        wf,
        stream_fn_factory=_never_factory,
        script_resolver=lambda _run: _my_script,
    )

    assert result.outputs["s1"]["result"] == "SCRIPTOUT"
    assert stream_fn_called == []


async def test_agent_tools() -> None:
    """Agent node with tools=('echo',) receives the resolved tool."""
    from agent.core import AgentTool, AgentToolResult
    from ai.types import TextContent as TC

    tools_seen: list[tuple[AgentTool, ...]] = []

    async def _spy_execute(
        tool_call_id: str,
        params: dict[str, Any],
        cancel: Any = None,
        on_update: Any = None,
        ctx: Any = None,
    ) -> AgentToolResult:
        return AgentToolResult(content=(TC(text="echo_result"),))

    spy_tool = AgentTool(
        name="echo",
        description="spy echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        label="Echo",
        execute=_spy_execute,
    )

    from flow.tools import ToolRegistry

    registry = ToolRegistry()
    registry.register(spy_tool)

    original_init = Agent.__init__

    def _patching_init(self: Agent, stream_fn: Any, **kwargs: Any) -> None:
        tools_arg = kwargs.get("tools")
        if tools_arg is not None:
            tools_seen.append(tuple(tools_arg))
        original_init(self, stream_fn, **kwargs)

    import agent.agent as _agent_module

    _agent_module.Agent.__init__ = _patching_init  # type: ignore[method-assign]
    try:
        factory = _make_stream_fn({"m": "agent_result"})
        wf = WorkflowDef(
            name="tools_test",
            provider="fake",
            entry="n1",
            default_model="m",
            nodes=(NodeDef(id="n1", model="m", prompt="do work", output_ports=(Port("out"),), tools=("echo",)),),
            edges=(),
        )

        result = await execute(wf, stream_fn_factory=factory, tool_registry=registry)
    finally:
        _agent_module.Agent.__init__ = original_init  # type: ignore[method-assign]

    assert result.outputs["n1"]["out"] == "agent_result"
    assert len(tools_seen) == 1
    assert tools_seen[0] == (spy_tool,)


# ---------------------------------------------------------------------------
# output_schema / submit_result integration
# ---------------------------------------------------------------------------


def _make_submit_result_factory(result_obj: dict[str, Any]) -> Any:
    """Return a stream_fn_factory whose agent emits a submit_result ToolCall then stops."""

    def _factory(model: str) -> Any:
        call_count: list[int] = [0]

        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            stream: AiEventStream[AssistantMessage] = AiEventStream()

            if call_count[0] == 0:
                call_count[0] += 1
                tool_call = ToolCall(id="tc-1", name="submit_result", arguments={"result": result_obj})
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


def _make_no_submit_factory() -> Any:
    """Return a stream_fn_factory whose agent never calls submit_result."""

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            msg = AssistantMessage(content=(TextContent(text="I am done"),), stop_reason="stop")
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


async def test_output_schema_success() -> None:
    """Node with output_schema stores JSON of the submitted result in its output port."""
    valid_obj = {"summary": "all good", "score": 42}

    wf = WorkflowDef(
        name="schema_test",
        provider="fake",
        entry="n1",
        default_model="m",
        nodes=(
            NodeDef(
                id="n1",
                model="m",
                prompt="summarise",
                output_ports=(Port("out"),),
                output_schema=(("summary", "string"), ("score", "integer")),
            ),
        ),
        edges=(),
    )

    factory = _make_submit_result_factory(valid_obj)
    result = await execute(wf, stream_fn_factory=factory)

    assert "out" in result.outputs["n1"]
    parsed = json.loads(result.outputs["n1"]["out"])
    assert parsed == valid_obj


async def test_output_schema_missing_submission() -> None:
    """Agent that never calls submit_result raises WorkflowExecutionError."""
    wf = WorkflowDef(
        name="schema_missing",
        provider="fake",
        entry="n1",
        default_model="m",
        nodes=(
            NodeDef(
                id="n1",
                model="m",
                prompt="summarise",
                output_ports=(Port("out"),),
                output_schema=(("summary", "string"),),
            ),
        ),
        edges=(),
    )

    factory = _make_no_submit_factory()
    import pytest

    with pytest.raises(WorkflowExecutionError, match="did not submit a result"):
        await execute(wf, stream_fn_factory=factory)
