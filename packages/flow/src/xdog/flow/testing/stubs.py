"""flow.testing.stubs — a case's stubs, in the shape the executor consults.

This is the only object the executor sees in test mode.  It answers agent turns,
optionally short-circuits subflow and script nodes, and records enough about each
decision to explain a failure afterwards.

Two invariants make it safe:

* Every agent node is answered here, whatever its ``backend`` — so no provider or
  CLI is constructed and a test cannot reach the network by accident.  A node with
  no stub raises :class:`StubMiss` rather than falling back to anything real.
* Selection never depends on completion order.  ``round`` is derived from the
  scheduler's activation id and ``index`` from the fanned array position, so a
  fan-out with three concurrent instances resolves the same way every run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from xdog.flow.errors import WorkflowExecutionError
from xdog.flow.models import NodeDef, agent_submits_object
from xdog.flow.testing.match import matches
from xdog.flow.testing.models import Case, NodeStub, StubRule


class StubMiss(WorkflowExecutionError):
    """No stub answered an activation — the case cannot proceed honestly."""


@dataclass
class StubCall:
    """One recorded stub decision, used to render the trace and failures."""

    node_id: str
    round: int
    fan_index: int | None
    inputs: dict[str, object]
    rule: int | None  # index of the rule that fired, or None on a miss


@dataclass
class CaseStubs:
    """The :class:`~flow.runners.NodeStubs` implementation for one case."""

    case: Case
    calls: list[StubCall] = field(default_factory=list)
    _rounds: dict[str, list[int]] = field(default_factory=dict)
    _fired: dict[tuple[str, int], int] = field(default_factory=dict)

    # -- NodeStubs -----------------------------------------------------------
    def agent(
        self,
        node: NodeDef,
        *,
        inputs: Mapping[str, object],
        step: int,
        fan_index: int | None,
    ) -> tuple[object, int]:
        stub = self.case.agents.get(node.id)
        if stub is None:
            raise StubMiss(
                f"agent node {node.id!r} has no stub; add it under 'agents' "
                f"(every agent node must be stubbed so a test never calls a model)"
            )
        rule = self._select(stub, inputs=inputs, step=step, fan_index=fan_index)
        return _agent_value(node, rule.then), rule.tokens

    def subflow(
        self,
        node: NodeDef,
        *,
        inputs: Mapping[str, object],
        step: int,
    ) -> dict[str, object] | None:
        stub = self.case.subflows.get(node.id)
        if stub is None:
            return None  # run the child for real
        return dict(self._select(stub, inputs=inputs, step=step, fan_index=None).then)

    def script(
        self,
        node: NodeDef,
        *,
        inputs: Mapping[str, object],
        step: int,
        fan_index: int | None,
    ) -> dict[str, object] | None:
        stub = self.case.scripts.get(node.id)
        if stub is None:
            return None  # run the script for real
        return dict(self._select(stub, inputs=inputs, step=step, fan_index=fan_index).then)

    # -- selection -----------------------------------------------------------
    def _select(
        self,
        stub: NodeStub,
        *,
        inputs: Mapping[str, object],
        step: int,
        fan_index: int | None,
    ) -> StubRule:
        round_ = self._round(stub.node_id, step)
        snapshot = dict(inputs)
        for i, rule in enumerate(stub.rules):
            if self._fits(rule, inputs=snapshot, round_=round_, fan_index=fan_index):
                self._fired[(stub.node_id, i)] = self._fired.get((stub.node_id, i), 0) + 1
                self.calls.append(
                    StubCall(stub.node_id, round_, fan_index, snapshot, rule=i)
                )
                return rule
        self.calls.append(StubCall(stub.node_id, round_, fan_index, snapshot, rule=None))
        raise StubMiss(_miss_message(stub, snapshot, round_, fan_index))

    @staticmethod
    def _fits(
        rule: StubRule,
        *,
        inputs: Mapping[str, object],
        round_: int,
        fan_index: int | None,
    ) -> bool:
        if rule.round is not None and rule.round != round_:
            return False
        if rule.index is not None and rule.index != fan_index:
            return False
        return not (rule.when is not None and not matches(rule.when, dict(inputs)))

    def _round(self, node_id: str, step: int) -> int:
        """1-based activation ordinal for *node_id*.

        Derived from the scheduler's step rather than a call counter: a fan-out
        node's instances share one step, so they share one round, and a looped
        node's rounds stay in iteration order even though instances are concurrent.
        """
        seen = self._rounds.setdefault(node_id, [])
        if step not in seen:
            seen.append(step)
        return seen.index(step) + 1

    # -- reporting -----------------------------------------------------------
    def unused_rules(self) -> list[tuple[str, int, StubRule]]:
        """Selector-bearing rules that never fired for a node that *did* run.

        A rule that never matched is almost always a stale selector (``round: 3``
        against a loop that ran twice), and silently falling through to the default
        would leave the case asserting something other than what it says.  Nodes
        that never ran are exempt — a stub kept for a branch this case does not
        take is legitimate.
        """
        stale: list[tuple[str, int, StubRule]] = []
        for group in (self.case.agents, self.case.subflows, self.case.scripts):
            for node_id, stub in group.items():
                if not any(c.node_id == node_id for c in self.calls):
                    continue
                for i, rule in enumerate(stub.rules):
                    if not rule.is_default and (node_id, i) not in self._fired:
                        stale.append((node_id, i, rule))
        return stale

    def stubbed_nodes(self) -> set[str]:
        return {*self.case.agents, *self.case.subflows, *self.case.scripts}


def _agent_value(node: NodeDef, ports: Mapping[str, object]) -> object:
    """Reshape a stub's port dict into what a real agent turn would have returned.

    The multi-port-vs-single-value rule lives in
    :func:`~flow.models.agent_submits_object`, shared with the executor's store and
    fan-projection paths — so a stub cannot drift from production behaviour.
    Returning the provider's shape keeps the stub on the production path, where the
    node's required-field check and ``to_state`` coercion still validate it.
    """
    if agent_submits_object(node):
        return dict(ports)
    declared = [p.name for p in node.output_ports]
    return ports.get(declared[0]) if declared else None


def _miss_message(
    stub: NodeStub,
    inputs: Mapping[str, object],
    round_: int,
    fan_index: int | None,
) -> str:
    where = f"round {round_}" + (f", index {fan_index}" if fan_index is not None else "")
    lines = [
        f"{stub.node_id}: no stub rule matched ({where})",
        f"  inputs  {dict(inputs)}",
    ]
    for i, rule in enumerate(stub.rules):
        lines.append(f"  rule[{i}]  {rule.describe()}")
    return "\n".join(lines)
