# Task: metrics aggregation — an in-process collector over the P3 event stream

Add a zero-dependency metrics aggregator for the `flow` package. flow already
emits P3 lifecycle events (`NodeStarted` / `NodeFinished` / `NodeFailed`) via the
`execute(on_event=...)` callback. This task adds a small `MetricsCollector` that
CONSUMES those events and aggregates them into a readable metrics snapshot — per
node and per run — so a caller can see, after a run, how many times each node
ran, how long it took, how many tokens it used, and how many times it failed.

This is deliberately single-machine and dependency-free: NO OpenTelemetry, NO
Prometheus, NO new third-party imports. It fits flow's kernel positioning — a
pure add-on over the existing `on_event` hook. **Do not modify the executor**; the
collector is just an `on_event` consumer plus a snapshot method.

Everything is under `packages/flow/`. `mypy --strict`, `ruff` line-length 120.

## 1. New module — `packages/flow/src/flow/telemetry.py`

Define frozen dataclasses for the aggregated numbers and a collector:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from flow.events import FlowEvent, NodeStarted, NodeFinished, NodeFailed

@dataclass(frozen=True)
class NodeMetrics:
    node_id: str
    runs: int          # NodeFinished count (a looped/reused node runs >1)
    failures: int      # NodeFailed count
    total_duration_s: float
    total_tokens: int

    @property
    def avg_duration_s(self) -> float:
        return self.total_duration_s / self.runs if self.runs else 0.0

@dataclass(frozen=True)
class RunMetrics:
    nodes: tuple[NodeMetrics, ...]   # one per node id that produced any event, stable order
    total_runs: int                  # sum of NodeFinished across all nodes
    total_failures: int
    total_duration_s: float          # sum of per-node durations (NOT wall clock)
    total_tokens: int


class MetricsCollector:
    """Aggregates flow's P3 lifecycle events into a RunMetrics snapshot.

    Usage:
        mc = MetricsCollector()
        await execute(wf, on_event=mc)          # MetricsCollector is callable
        metrics = mc.snapshot()
    """
    def __init__(self) -> None: ...
    def __call__(self, ev: FlowEvent) -> None: ...   # the on_event callback
    def snapshot(self) -> RunMetrics: ...
```

Behaviour:
- The collector is **callable** so it can be passed directly as `on_event`.
- On `NodeFinished(node_id, ..., duration_s, tokens)`: increment that node's
  `runs`, add `duration_s` and `tokens`.
- On `NodeFailed(node_id, ..., duration_s, ...)`: increment `failures`, add
  `duration_s` (a failed node still consumed time).
- `NodeStarted` needs no aggregation (starts equal finishes+failures+in-flight);
  you may ignore it or use it only to register the node id ordering.
- Track per-node counters internally (e.g. a dict keyed by node_id) and build the
  immutable `RunMetrics` in `snapshot()`. Node order in `snapshot().nodes` should
  be **first-seen order** (deterministic), not dict/hash order.
- Thread-safety: the executor delivers events from one event loop; a plain dict is
  fine, no lock needed. Keep it simple.
- Zero external dependencies. Fully type-annotated.

## 2. Export it — `packages/flow/src/flow/__init__.py`

Add `MetricsCollector`, `NodeMetrics`, `RunMetrics` to the package's public
exports (follow the existing `__all__` / import style there).

## 3. Do NOT touch the executor or codegen

The collector rides entirely on the existing `on_event` callback. The executor,
events module, and codegen are unchanged. (The generated module already logs
lifecycle events to `flow.generated.events`; metrics aggregation is an
interpreter-side, caller-driven concern, so codegen needs no change and the
interpret==compile tests stay green.)

## 4. Tests — `packages/flow/tests/test_telemetry.py`

- **Aggregates a simple run**: build a 2-node workflow (a script node + an agent
  node with a fake stream), run it with `on_event=MetricsCollector()`, and assert
  the snapshot has one `NodeMetrics` per node with `runs == 1`, non-negative
  durations, and the agent node's `total_tokens` matching the stubbed usage.
- **Counts loop iterations**: a bounded-loop workflow where a node runs twice —
  assert that node's `runs == 2` and `total_duration_s` is the sum.
- **Counts failures**: a node that raises (default on_error=fail) produces
  `failures >= 1` in that node's metrics before the run raises; the run-level
  `total_failures` reflects it. (Collect events up to the raise.)
- **Run-level totals**: `snapshot().total_runs` / `total_tokens` /
  `total_failures` equal the sums across nodes.
- **Deterministic order**: `snapshot().nodes` is in first-seen order.
- Construct fake streams the same way the existing tests do (see
  `test_events.py` / `test_executor.py`).

## 5. Gate — must be green

```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```

Run them yourself and fix anything you introduce. Do not weaken existing tests,
do not add third-party dependencies, and do not change the executor / events /
codegen.
