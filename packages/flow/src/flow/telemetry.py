"""flow.telemetry — zero-dependency metrics aggregator over the P3 event stream."""

from __future__ import annotations

from dataclasses import dataclass

from flow.events import FlowEvent, NodeFailed, NodeFinished, NodeStarted


@dataclass(frozen=True)
class NodeMetrics:
    node_id: str
    runs: int  # NodeFinished count
    failures: int  # NodeFailed count
    total_duration_s: float
    total_tokens: int

    @property
    def avg_duration_s(self) -> float:
        return self.total_duration_s / self.runs if self.runs else 0.0


@dataclass(frozen=True)
class RunMetrics:
    nodes: tuple[NodeMetrics, ...]  # one per node id, first-seen order
    total_runs: int
    total_failures: int
    total_duration_s: float
    total_tokens: int


@dataclass
class _NodeAccumulator:
    runs: int = 0
    failures: int = 0
    total_duration_s: float = 0.0
    total_tokens: int = 0


class MetricsCollector:
    """Aggregates flow's P3 lifecycle events into a RunMetrics snapshot.

    Usage::

        mc = MetricsCollector()
        await execute(wf, on_event=mc)   # MetricsCollector is callable
        metrics = mc.snapshot()
    """

    def __init__(self) -> None:
        self._order: list[str] = []
        self._accumulators: dict[str, _NodeAccumulator] = {}

    def _get_or_create(self, node_id: str) -> _NodeAccumulator:
        if node_id not in self._accumulators:
            self._order.append(node_id)
            self._accumulators[node_id] = _NodeAccumulator()
        return self._accumulators[node_id]

    def __call__(self, ev: FlowEvent) -> None:
        if isinstance(ev, NodeStarted):
            self._get_or_create(ev.node_id)
        elif isinstance(ev, NodeFinished):
            acc = self._get_or_create(ev.node_id)
            acc.runs += 1
            acc.total_duration_s += ev.duration_s
            acc.total_tokens += ev.tokens
        elif isinstance(ev, NodeFailed):
            acc = self._get_or_create(ev.node_id)
            acc.failures += 1
            acc.total_duration_s += ev.duration_s

    def snapshot(self) -> RunMetrics:
        nodes = tuple(
            NodeMetrics(
                node_id=nid,
                runs=self._accumulators[nid].runs,
                failures=self._accumulators[nid].failures,
                total_duration_s=self._accumulators[nid].total_duration_s,
                total_tokens=self._accumulators[nid].total_tokens,
            )
            for nid in self._order
        )
        return RunMetrics(
            nodes=nodes,
            total_runs=sum(n.runs for n in nodes),
            total_failures=sum(n.failures for n in nodes),
            total_duration_s=sum(n.total_duration_s for n in nodes),
            total_tokens=sum(n.total_tokens for n in nodes),
        )
