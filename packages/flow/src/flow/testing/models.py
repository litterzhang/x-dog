"""flow.testing.models — the parsed shape of a ``*.test.json`` suite.

These are the *validated* in-memory forms; :mod:`flow.testing.loader` builds them
from JSON and is the only place that reports authoring errors.  Everything here is
frozen so a suite can be reused across runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class StubRule:
    """One branch of a node's stub, selected by up to three predicates.

    All supplied selectors must hold for the rule to fire; a rule with none is the
    default branch.  Every selector is stable under concurrency:

    * ``when``   — deep-subset match on the activation's inputs (a value match, so
      fan-out completion order is irrelevant).
    * ``index``  — the instance's position in the fanned array, **not** the order it
      finished.  Fan-out nodes only.
    * ``round``  — which scheduler activation this is for the node, 1-based; a
      fan-out node's instances all share one round.  This is the loop-iteration
      ordinal, and loops are sequential.
    """

    then: dict[str, object]
    when: dict[str, object] | None = None
    index: int | None = None
    round: int | None = None
    tokens: int = 0

    @property
    def is_default(self) -> bool:
        return self.when is None and self.index is None and self.round is None

    def describe(self) -> str:
        """One-line rendering of this rule's selectors, for failure reports."""
        parts: list[str] = []
        if self.when is not None:
            parts.append(f"when {self.when}")
        if self.index is not None:
            parts.append(f"index {self.index}")
        if self.round is not None:
            parts.append(f"round {self.round}")
        return " + ".join(parts) if parts else "(default)"


@dataclass(frozen=True)
class NodeStub:
    """A node's stub: an ordered rule list, first match wins.

    A constant stub in the JSON (a bare port dict) parses to a single default rule,
    so the runtime has exactly one shape to reason about.
    """

    node_id: str
    rules: tuple[StubRule, ...]


@dataclass(frozen=True)
class Expect:
    """A case's expectations.

    ``outcome`` is a closed three-way choice rather than a pile of booleans: a run
    either completes, fails, or pauses, and each carries different evidence.
    ``output`` and ``calls`` are partial — they assert intent, not full structure,
    so adding a node or an output field does not break existing cases.
    """

    outcome: Literal["success", "error", "paused"] = "success"
    error: str | None = None  # substring of str(exc), when outcome == "error"
    paused: str | None = None  # node id, when outcome == "paused"
    output: dict[str, object] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Case:
    """One test case: how to drive the workflow, and what to expect back."""

    name: str
    inputs: dict[str, object] = field(default_factory=dict)
    signals: frozenset[str] = frozenset()
    max_tokens: int | None = None
    agents: dict[str, NodeStub] = field(default_factory=dict)
    subflows: dict[str, NodeStub] = field(default_factory=dict)
    scripts: dict[str, NodeStub] = field(default_factory=dict)
    expect: Expect = field(default_factory=Expect)


@dataclass(frozen=True)
class Suite:
    """A loaded ``*.test.json`` plus the workflow it targets."""

    path: Path
    workflow_path: Path
    cases: tuple[Case, ...]
