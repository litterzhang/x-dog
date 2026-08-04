"""Pure frontier/token transition semantics shared by interpreter and codegen."""

from __future__ import annotations

from flow.frontier import (
    build_frontier_spec,
    complete_batch,
    is_quiescent,
    new_frontier_state,
    render_frontier_runtime,
    take_ready,
)
from flow.models import Condition, EdgeDef, NodeDef, WorkflowDef, edge_identities


def _workflow(nodes: tuple[str, ...], edges: tuple[EdgeDef, ...], *, entry: str = "") -> WorkflowDef:
    return WorkflowDef(
        name="frontier",
        provider="",
        entry=entry,
        nodes=tuple(NodeDef(id=node, type="script") for node in nodes),
        edges=edges,
    )


def _finish(
    spec: dict[str, object],
    state: dict[str, object],
    nodes: list[tuple[str, int, tuple[str, ...]]],
    edge_results: dict[str, bool],
    loop_counts: dict[str, int],
) -> str | None:
    return complete_batch(
        spec,
        state,
        [(node, epoch, edge_results) for node, epoch, _enabled in nodes],
        loop_counts,
    )


def test_forward_join_waits_for_all_predecessors_and_keeps_enabled_edges() -> None:
    wf = _workflow(
        ("b", "c", "a"),
        (
            EdgeDef(src="b", dst="a"),
            EdgeDef(src="c", dst="a"),
        ),
    )
    edge_b, edge_c = edge_identities(wf)
    spec = build_frontier_spec(wf)
    state = new_frontier_state(spec)

    initial = take_ready(spec, state)
    assert [node for node, _epoch, _enabled in initial] == ["b", "c"]

    assert _finish(spec, state, [initial[0]], {edge_b: True}, {}) is None
    assert take_ready(spec, state) == []

    assert _finish(spec, state, [initial[1]], {edge_c: True}, {}) is None
    assert take_ready(spec, state) == [("a", 0, (edge_b, edge_c))]


def test_false_forward_edge_does_not_map_input_but_join_still_waits_for_source() -> None:
    wf = _workflow(
        ("b", "c", "a"),
        (
            EdgeDef(src="b", dst="a", when=Condition(op="equals", value="b", text="b")),
            EdgeDef(src="c", dst="a", when=Condition(op="equals", value="c", text="never")),
        ),
    )
    edge_b, edge_c = edge_identities(wf)
    spec = build_frontier_spec(wf)
    state = new_frontier_state(spec)
    initial = take_ready(spec, state)

    assert _finish(spec, state, initial, {edge_b: True, edge_c: False}, {}) is None
    assert take_ready(spec, state) == [("a", 0, (edge_b,))]


def test_all_false_forward_edges_leave_destination_unreached_and_quiescent() -> None:
    wf = _workflow(
        ("b", "c", "a"),
        (
            EdgeDef(src="b", dst="a", when=Condition(op="equals", value="x", text="no")),
            EdgeDef(src="c", dst="a", when=Condition(op="equals", value="y", text="no")),
        ),
    )
    edge_b, edge_c = edge_identities(wf)
    spec = build_frontier_spec(wf)
    state = new_frontier_state(spec)
    initial = take_ready(spec, state)

    _finish(spec, state, initial, {edge_b: False, edge_c: False}, {})
    assert take_ready(spec, state) == []
    assert is_quiescent(state) is True


def _two_source_loop(
    *,
    first_max: int = 3,
    second_max: int = 3,
    first_strict: bool = False,
    second_strict: bool = False,
) -> WorkflowDef:
    return _workflow(
        ("a", "b", "c"),
        (
            EdgeDef(src="a", dst="b"),
            EdgeDef(src="a", dst="c"),
            EdgeDef(
                src="b",
                dst="a",
                when=Condition(op="equals", value="yes", text="yes"),
                loop_max=first_max,
                loop_strict=first_strict,
            ),
            EdgeDef(
                src="c",
                dst="a",
                when=Condition(op="equals", value="yes", text="yes"),
                loop_max=second_max,
                loop_strict=second_strict,
            ),
        ),
        entry="a",
    )


def _run_first_loop_generation(
    wf: WorkflowDef,
    *,
    first_condition: bool = True,
    second_condition: bool = True,
    counts: dict[str, int] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, int], tuple[str, ...], str | None]:
    spec = build_frontier_spec(wf)
    state = new_frontier_state(spec)
    edge_ab, edge_ac, edge_ba, edge_ca = edge_identities(wf)
    loop_counts = {} if counts is None else dict(counts)

    a = take_ready(spec, state)
    assert a == [("a", 0, ())]
    _finish(spec, state, a, {edge_ab: True, edge_ac: True}, loop_counts)
    branches = take_ready(spec, state)
    assert [node for node, _epoch, _enabled in branches] == ["b", "c"]

    error = _finish(
        spec,
        state,
        branches,
        {edge_ba: first_condition, edge_ca: second_condition},
        loop_counts,
    )
    return spec, state, loop_counts, (edge_ba, edge_ca), error


def test_loop_group_requires_all_sources_then_reactivates_destination_once() -> None:
    wf = _two_source_loop()
    spec = build_frontier_spec(wf)
    state = new_frontier_state(spec)
    edge_ab, edge_ac, edge_ba, edge_ca = edge_identities(wf)
    counts: dict[str, int] = {}

    a = take_ready(spec, state)
    _finish(spec, state, a, {edge_ab: True, edge_ac: True}, counts)
    b, c = take_ready(spec, state)

    assert _finish(spec, state, [b], {edge_ba: True}, counts) is None
    assert take_ready(spec, state) == []
    assert counts == {}

    assert _finish(spec, state, [c], {edge_ca: True}, counts) is None
    assert counts == {edge_ba: 1, edge_ca: 1}
    assert take_ready(spec, state) == [("a", 1, (edge_ba, edge_ca))]


def test_false_loop_member_closes_group_without_increment_or_reactivation() -> None:
    wf = _two_source_loop()
    spec, state, counts, _loop_edges, error = _run_first_loop_generation(
        wf,
        first_condition=True,
        second_condition=False,
    )
    assert error is None
    assert counts == {}
    assert take_ready(spec, state) == []
    assert is_quiescent(state) is True


def test_shorter_plain_loop_stops_mixed_group_normally() -> None:
    wf = _two_source_loop(first_max=1, second_max=5, second_strict=True)
    edge_ba, edge_ca = edge_identities(wf)[2:]
    spec, state, counts, _loop_edges, error = _run_first_loop_generation(
        wf,
        counts={edge_ba: 1, edge_ca: 1},
    )
    assert error is None
    assert counts == {edge_ba: 1, edge_ca: 1}
    assert take_ready(spec, state) == []


def test_exhausted_strict_member_wins_over_plain_member() -> None:
    wf = _two_source_loop(first_max=1, second_max=1, first_strict=False, second_strict=True)
    edge_ba, edge_ca = edge_identities(wf)[2:]
    _spec, _state, counts, _loop_edges, error = _run_first_loop_generation(
        wf,
        counts={edge_ba: 1, edge_ca: 1},
    )
    assert error == edge_ca
    assert counts == {edge_ba: 1, edge_ca: 1}


def test_rendered_frontier_runtime_is_standalone_and_compiles() -> None:
    source = render_frontier_runtime()
    assert "from flow" not in source
    assert "import flow" not in source
    namespace: dict[str, object] = {}
    exec(compile(source, "<frontier-runtime>", "exec"), namespace)  # noqa: S102
    assert callable(namespace["take_ready"])
    assert callable(namespace["complete_batch"])


def test_loop_generation_discards_old_completion_and_arrivals() -> None:
    wf = _two_source_loop()
    spec, state, counts, (edge_ba, edge_ca), error = _run_first_loop_generation(wf)
    assert error is None

    a_next = take_ready(spec, state)
    assert a_next == [("a", 1, (edge_ba, edge_ca))]
    generations = state["generations"]
    completed = state["completed"]
    assert isinstance(generations, dict)
    assert isinstance(completed, dict)
    assert generations == {"a": 1, "b": 1, "c": 1}
    assert completed == {}
    assert counts == {edge_ba: 1, edge_ca: 1}
