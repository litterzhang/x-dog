"""Tests for flow.executor — linear + parallel workflow execution.

Data flows through node-private ports: a node reads its input ports (fed by
incoming edge mappings) and writes its output ports.  ``result.runtime`` is the
run container; ``runtime["state"][node_id][port]`` holds real-node outputs,
``runtime["in"]`` the initial state, and ``runtime["out"]`` the collected
``$output``.  Prompt ``{{x}}`` is PORT-LOCAL (reads this node's input port ``x``).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from agent.agent import Agent
from ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from ai.utils.event_stream import EventStream as AiEventStream
from flow.errors import WorkflowExecutionError
from flow.executor import ExecResult, execute
from flow.models import IN_NODE_ID, OUT_NODE_ID, Condition, EdgeDef, NodeDef, Port, RetryPolicy, WorkflowDef

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
    assert result.runtime["state"]["node1"]["step1_out"] == "result_from_a"
    assert result.runtime["state"]["node2"]["step2_out"] == "result_from_b"


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

    assert "out1" in result.runtime["state"]["node1"]
    assert "out2" in result.runtime["state"]["node2"]


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

    assert result.runtime["state"]["a"]["va"] == "ok"
    assert result.runtime["state"]["b"]["vb"] == "ok"
    assert result.runtime["state"]["c"]["vc"] == "ok"


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
    assert result.runtime["state"].get("only", {}) == {}


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


async def test_inputs_override_seed() -> None:
    """execute(inputs=...) overrides the workflow's $in defaults key-by-key."""

    async def _echo(ctx: Any, a: Any, b: Any) -> str:
        return f"{a}+{b}"

    wf = WorkflowDef(
        name="ov",
        provider="fake",
        entry="mk",
        default_model="m",
        initial_state=(("a", "1"), ("b", "2")),
        nodes=(
            NodeDef(
                id="mk",
                type="script",
                run="dummy:fn",
                input_ports=(Port("a", "integer"), Port("b", "integer")),
                output_ports=(Port("s"),),
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="mk", mapping=(("a", "a"), ("b", "b"))),),
    )

    # override only `a`; `b` keeps its workflow default
    result = await execute(
        wf,
        stream_fn_factory=_make_stream_fn({}),
        script_resolver=lambda _r: _echo,
        inputs={"a": "40"},
    )

    assert result.runtime["in"] == {"a": "40", "b": "2"}
    assert result.runtime["state"]["mk"]["s"] == "40+2"


async def test_no_inputs_uses_defaults() -> None:
    """Without inputs=, the $in seed is exactly the workflow's initial_state."""

    async def _echo(ctx: Any, a: Any) -> str:
        return str(a)

    wf = WorkflowDef(
        name="def",
        provider="fake",
        entry="mk",
        default_model="m",
        initial_state=(("a", "7"),),
        nodes=(
            NodeDef(
                id="mk",
                type="script",
                run="dummy:fn",
                input_ports=(Port("a", "integer"),),
                output_ports=(Port("s"),),
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="mk", mapping=(("a", "a"),)),),
    )

    result = await execute(wf, stream_fn_factory=_make_stream_fn({}), script_resolver=lambda _r: _echo)

    assert result.runtime["in"] == {"a": "7"}
    assert result.runtime["state"]["mk"]["s"] == "7"


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

    assert result.runtime["state"]["b"]["vb"] == "output_from_b"
    assert result.runtime["state"]["c"]["vc"] == "output_from_c"
    assert result.runtime["state"]["d"]["vd"] == "output_from_d"

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

    assert result.runtime["state"]["a"]["va"] == "no_match"
    assert "b" not in result.runtime["state"]


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
    assert result.runtime["state"]["review"]["verdict"] == "APPROVE"
    assert review_call[0] == 2

    # The stack is a per-node delta trace: write, review, write, review (looped
    # once), each frame recording the node's input namespace and output ports.
    stack = result.runtime["stack"]
    assert [f["node"] for f in stack] == ["write", "review", "write", "review"]
    assert [f["step"] for f in stack] == [0, 1, 2, 3]
    # the second review saw the second draft as its input (loop fed the revision)
    assert stack[3]["in"] == {"draft": "draft"}
    assert stack[3]["out"] == {"verdict": "APPROVE"}
    # ctx reflects the last node to run
    assert result.runtime["ctx"] == {"step": 3, "node_id": "review", "workflow_name": "review_loop"}


async def test_optional_input_absent_on_first_pass_then_fed_by_loop() -> None:
    """An optional input port is absent on pass 1 (interpolates to '') and is fed
    by the loop back-edge on later passes."""
    review_responses = ["REVISE: more", "APPROVE"]
    review_call: list[int] = [0]

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            if model == "draft-model":
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
        name="opt_loop",
        provider="fake",
        entry="draft",
        default_model="draft-model",
        initial_state=(("topic", "T"),),
        nodes=(
            NodeDef(
                id="draft",
                model="draft-model",
                prompt="topic={{topic}} fb={{feedback}}",
                input_ports=(Port("topic"), Port("feedback", optional=True)),
                output_ports=(Port("answer"),),
            ),
            NodeDef(
                id="critic",
                model="critic-model",
                prompt="review {{answer}}",
                input_ports=(Port("answer"),),
                output_ports=(Port("feedback"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="draft", mapping=(("topic", "topic"),)),
            EdgeDef(src="draft", dst="critic", mapping=(("answer", "answer"),)),
            EdgeDef(
                src="critic",
                dst="draft",
                mapping=(("feedback", "feedback"),),
                when=Condition(op="contains", value="{{feedback}}", text="REVISE"),
                loop_max=2,
            ),
        ),
    )

    result = await execute(wf, stream_fn_factory=_factory)

    # the loop ran once: draft, critic, draft, critic
    stack = result.runtime["stack"]
    assert [f["node"] for f in stack] == ["draft", "critic", "draft", "critic"]
    # pass 1: draft's optional feedback port is absent from its inputs
    assert "feedback" not in stack[0]["in"]
    assert stack[0]["in"] == {"topic": "T"}
    # pass 2: the loop fed feedback back into draft
    assert stack[2]["in"].get("feedback") == "REVISE: more"


async def test_output_sink_collects_declared_outputs() -> None:
    """Edges targeting $output populate runtime['out']; a looped writer's latest wins."""
    wf = WorkflowDef(
        name="out_wf",
        provider="fake",
        entry="mk",
        default_model="m",
        initial_state=(("a", "5"),),
        nodes=(
            NodeDef(
                id="mk",
                type="script",
                input_ports=(Port("a", "integer"),),
                code="def mk(ctx, a):\n    return a * 2",
                output_ports=(Port("doubled", "integer"),),
            ),
        ),
        edges=(
            EdgeDef(src=IN_NODE_ID, dst="mk", mapping=(("a", "a"),)),
            EdgeDef(src="mk", dst=OUT_NODE_ID, mapping=(("doubled", "result"),)),
        ),
    )

    result = await execute(wf, stream_fn_factory=_make_stream_fn({}))

    # $in / $output kept separate from the real-node state map
    assert result.runtime["in"] == {"a": "5"}
    assert result.runtime["state"] == {"mk": {"doubled": "10"}}
    assert result.runtime["out"] == {"result": "10"}


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

    assert result.runtime["state"]["s1"]["result"] == "SCRIPTOUT"
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

    assert result.runtime["state"]["n1"]["out"] == "agent_result"
    assert len(tools_seen) == 1
    assert tools_seen[0] == (spy_tool,)


async def test_agent_custom_tool_from_manifest() -> None:
    """A workflow tool manifest is loaded from base_dir and reaches the agent."""
    from pathlib import Path

    from agent.core import AgentTool

    fixtures = Path(__file__).parent / "fixtures"
    tools_seen: list[tuple[AgentTool, ...]] = []

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
            name="manifest_test",
            provider="fake",
            entry="n1",
            default_model="m",
            nodes=(NodeDef(id="n1", model="m", prompt="do work", output_ports=(Port("out"),), tools=("reverse",)),),
            edges=(),
            tool_refs=(("reverse", "mytools:make_reverse"),),
        )

        result = await execute(wf, stream_fn_factory=factory, base_dir=fixtures)
    finally:
        _agent_module.Agent.__init__ = original_init  # type: ignore[method-assign]

    assert result.runtime["state"]["n1"]["out"] == "agent_result"
    assert len(tools_seen) == 1
    # The loaded tool reached the agent under its MANIFEST name (not the internal one).
    assert len(tools_seen[0]) == 1
    assert tools_seen[0][0].name == "reverse"


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

    assert "out" in result.runtime["state"]["n1"]
    parsed = json.loads(result.runtime["state"]["n1"]["out"])
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


# ---------------------------------------------------------------------------
# web_search (agent builtin, enabled per-node)
# ---------------------------------------------------------------------------


async def test_agent_web_search_tool_enabled() -> None:
    """A node with web_search=True gets a web_search tool backed by the factory.

    The fake stream emits a web_search ToolCall on the first turn, then stops;
    we assert the injected search fn was invoked with the web_search_model.
    """
    searched: list[tuple[str, str]] = []

    def _ws_factory(model: str) -> Any:
        async def _search(query: str) -> str:
            searched.append((model, query))
            return f"web result for {query}"

        return _search

    def _factory(model: str) -> Any:
        turn = [0]

        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            stream: AiEventStream[AssistantMessage] = AiEventStream()
            if turn[0] == 0:
                turn[0] += 1
                call = ToolCall(id="ws1", name="web_search", arguments={"query": "depin news"})
                msg = AssistantMessage(content=(call,), stop_reason="toolUse")
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

    wf = WorkflowDef(
        name="ws",
        provider="fake",
        entry="a",
        default_model="m",
        initial_state=(("q", "depin"),),
        nodes=(
            NodeDef(
                id="a",
                type="agent",
                prompt="research {{q}}",
                input_ports=(Port("q"),),
                output_ports=(Port("r"),),
                web_search=True,
                web_search_model="gpt-5.5",
            ),
        ),
        edges=(EdgeDef(src=IN_NODE_ID, dst="a", mapping=(("q", "q"),)),),
    )

    result = await execute(wf, stream_fn_factory=_factory, web_search_fn_factory=_ws_factory)

    assert result.runtime["state"]["a"]["r"] == "done"
    # the search fn ran, bound to the web_search_model (not the node model)
    assert searched == [("gpt-5.5", "depin news")]


# ---------------------------------------------------------------------------
# Per-node retry policy
# ---------------------------------------------------------------------------


async def test_script_retry_succeeds_after_failures() -> None:
    """Script node that fails N times then succeeds: with retry.max=N the run succeeds."""
    call_count: list[int] = [0]
    n_failures = 2

    async def _flaky(ctx: Any) -> str:
        call_count[0] += 1
        if call_count[0] <= n_failures:
            raise RuntimeError(f"transient failure #{call_count[0]}")
        return "SUCCESS"

    wf = WorkflowDef(
        name="retry_test",
        provider="fake",
        entry="s1",
        default_model="m",
        nodes=(
            NodeDef(
                id="s1",
                type="script",
                run="dummy:fn",
                output_ports=(Port("result"),),
                retry=RetryPolicy(max=n_failures, backoff=0.0),
            ),
        ),
        edges=(),
    )

    result = await execute(
        wf,
        stream_fn_factory=_make_stream_fn({}),
        script_resolver=lambda _run: _flaky,
    )

    assert result.runtime["state"]["s1"]["result"] == "SUCCESS"
    assert call_count[0] == n_failures + 1


async def test_script_no_retry_raises_immediately() -> None:
    """Script node with retry=None raises on the first failure without retrying."""
    call_count: list[int] = [0]

    async def _always_fails(ctx: Any) -> str:
        call_count[0] += 1
        raise RuntimeError("always fails")

    wf = WorkflowDef(
        name="no_retry_test",
        provider="fake",
        entry="s1",
        default_model="m",
        nodes=(
            NodeDef(
                id="s1",
                type="script",
                run="dummy:fn",
                output_ports=(Port("result"),),
                retry=None,
            ),
        ),
        edges=(),
    )

    with pytest.raises(RuntimeError, match="always fails"):
        await execute(
            wf,
            stream_fn_factory=_make_stream_fn({}),
            script_resolver=lambda _run: _always_fails,
        )

    assert call_count[0] == 1


async def test_script_retry_exhausted_re_raises() -> None:
    """Script node with retry.max=1 raises after 2 total attempts."""
    call_count: list[int] = [0]

    async def _always_fails(ctx: Any) -> str:
        call_count[0] += 1
        raise RuntimeError("still failing")

    wf = WorkflowDef(
        name="retry_exhaust",
        provider="fake",
        entry="s1",
        default_model="m",
        nodes=(
            NodeDef(
                id="s1",
                type="script",
                run="dummy:fn",
                output_ports=(Port("result"),),
                retry=RetryPolicy(max=1, backoff=0.0),
            ),
        ),
        edges=(),
    )

    with pytest.raises(RuntimeError, match="still failing"):
        await execute(
            wf,
            stream_fn_factory=_make_stream_fn({}),
            script_resolver=lambda _run: _always_fails,
        )

    assert call_count[0] == 2  # 1 initial + 1 retry


async def test_agent_retry_succeeds_after_failures() -> None:
    """Agent node whose prompt() raises on the first N calls succeeds with retry.max=N.

    We patch Agent.prompt to raise on the first N invocations so the executor's
    retry loop sees real exceptions from the agent branch.
    """
    import agent.agent as _agent_module

    call_count: list[int] = [0]
    n_failures = 2
    original_prompt = _agent_module.Agent.prompt

    async def _flaky_prompt(self: Any, *args: Any, **kwargs: Any) -> Any:
        call_count[0] += 1
        if call_count[0] <= n_failures:
            raise RuntimeError(f"agent transient failure #{call_count[0]}")
        return await original_prompt(self, *args, **kwargs)

    _agent_module.Agent.prompt = _flaky_prompt  # type: ignore[method-assign]
    try:
        factory = _make_stream_fn({"m": "AGENT_OK"})
        wf = WorkflowDef(
            name="agent_retry_test",
            provider="fake",
            entry="a1",
            default_model="m",
            nodes=(
                NodeDef(
                    id="a1",
                    type="agent",
                    model="m",
                    prompt="hello",
                    output_ports=(Port("result"),),
                    retry=RetryPolicy(max=n_failures, backoff=0.0),
                ),
            ),
            edges=(),
        )

        result = await execute(wf, stream_fn_factory=factory)
    finally:
        _agent_module.Agent.prompt = original_prompt  # type: ignore[method-assign]

    assert result.runtime["state"]["a1"]["result"] == "AGENT_OK"
    assert call_count[0] == n_failures + 1


async def test_agent_without_web_search_has_no_search_tool() -> None:
    """A node without web_search never invokes the search factory."""
    searched: list[str] = []

    def _ws_factory(model: str) -> Any:
        async def _search(query: str) -> str:
            searched.append(query)
            return "x"

        return _search

    factory = _make_stream_fn({"m": "ok"})
    wf = WorkflowDef(
        name="no-ws",
        provider="fake",
        entry="a",
        default_model="m",
        nodes=(NodeDef(id="a", type="agent", model="m", prompt="hi", output_ports=(Port("r"),)),),
        edges=(),
    )

    result = await execute(wf, stream_fn_factory=factory, web_search_fn_factory=_ws_factory)

    assert result.runtime["state"]["a"]["r"] == "ok"
    assert searched == []
