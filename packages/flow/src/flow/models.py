"""flow.models — frozen dataclass domain model for workflow definitions.

Data flows between nodes through **named ports**, not a shared flat state.  Each
node declares ``input_ports`` and ``output_ports``; an :class:`EdgeDef` carries an
explicit ``mapping`` of ``(source_output_port, destination_input_port)`` pairs, so
``nodeA.output.x -> nodeB.input.a`` is spelled out rather than implied by shared
key names.  Initial workflow inputs are exposed as the output ports of a reserved
synthetic source node whose id is :data:`IN_NODE_ID`.  Workflow outputs are
collected by edges targeting a reserved synthetic sink node :data:`OUT_NODE_ID`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Reserved id for the synthetic source node whose output ports carry the
# workflow's initial ``state`` values.  Chosen to be a non-identifier so it can
# never collide with a user-authored node id.
IN_NODE_ID = "$in"

# Reserved id for the synthetic sink node that collects the workflow's outputs.
# Nodes wire their output ports to it via ordinary edges (``to: "$output"``); the
# collected mapping is exposed as the run's ``out`` result.  A dst-only mirror of
# :data:`IN_NODE_ID`; likewise a non-identifier so it can't collide.
OUT_NODE_ID = "$output"


@dataclass(frozen=True, init=False)
class Port:
    """A named, JSON-Schema-typed data port on a node.

    ``schema`` is a JSON Schema fragment describing the port's value: a scalar
    ``{"type": "integer"}``, a structured ``{"type": "object", "properties":
    {...}, "required": [...]}``, or ``{"type": "array", "items": {...}}``.  The
    six top-level type names (``string``/``integer``/``number``/``boolean``/
    ``array``/``object``) drive wire-format coercion; nested structure is
    validated (not re-coerced) by fastjsonschema.

    ``required`` marks an **input** port that must be fed by an edge (the default).
    ``required=False`` is the old ``optional``: a loop-carried value absent on the
    first pass interpolates to ``""`` / a script sees the type's zero-value.
    Ignored on output ports (they are always produced by their node).

    The constructor stays backward-compatible: ``Port("n", "integer")`` and
    ``Port("n", optional=True)`` still work, building the equivalent ``schema`` /
    ``required``.  A structured port passes ``schema=`` directly.  ``type`` is a
    convenience view of ``schema["type"]``.
    """

    name: str
    schema: dict[str, object]
    required: bool

    def __init__(
        self,
        name: str,
        type: str | None = None,  # noqa: A002 - legacy positional; builds schema
        optional: bool | None = None,
        *,
        schema: dict[str, object] | None = None,
        required: bool | None = None,
    ) -> None:
        if schema is None:
            schema = {"type": type or "string"}
        if required is None:
            required = True if optional is None else not optional
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "required", required)

    @property
    def type(self) -> str:
        """The port's top-level JSON type name (``schema['type']``)."""
        t = self.schema.get("type", "string")
        return t if isinstance(t, str) else "string"


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to retry a failed node, and the backoff between attempts."""

    max: int = 0  # number of RETRIES after the first attempt (0 = no retry)
    backoff: float = 0.0  # seconds; the delay before retry k is backoff * k


@dataclass(frozen=True)
class NodeDef:
    id: str
    type: Literal["agent", "script", "human", "subflow"] = "agent"
    signal: str = ""
    model: str | None = None
    system_prompt: str = ""
    prompt: str = ""
    tools: tuple[str, ...] = ()
    run: str | None = None
    # Data ports: each node reads its input ports and writes its output ports.
    input_ports: tuple[Port, ...] = ()
    output_ports: tuple[Port, ...] = ()
    # Inline script source (agent nodes leave this empty).
    code: str | None = None
    # Agent nodes: enable the built-in web_search tool.  ``web_search_model``
    # optionally overrides which model performs the search (some models don't
    # browse — e.g. Claude on Copilot — so a browsing model like gpt-5.5 can be
    # named here); falls back to the node/default model when None.
    web_search: bool = False
    web_search_model: str | None = None
    # Per-node retry policy: None means no retries (fail on first error).
    retry: RetryPolicy | None = None
    # Failure isolation: "fail" = propagate (default/fail-fast); "isolate" = capture
    # and continue sibling branches.
    on_error: Literal["fail", "isolate"] = "fail"
    # Determinism flag: when True, output is memoised keyed by (node_id, hash(inputs))
    # so the node is skipped on retry/resume when the same input was already processed.
    deterministic: bool = False
    # Sub-workflow (G5): for a ``type="subflow"`` node, the INLINE child workflow
    # run as one opaque node.  Its input_ports/output_ports are DERIVED from the
    # child's signature at load time (the author does not declare them).  Inline
    # (not a path ref) so codegen can embed it as a literal and recursion is
    # structurally impossible.  See docs/subflow.md.
    child: WorkflowDef | None = None

    @property
    def input_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.input_ports)

    @property
    def output_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.output_ports)


@dataclass(frozen=True)
class Condition:
    op: Literal["equals", "contains", "gt", "gte", "lt", "lte", "not", "and", "or"]
    value: str | None = None
    text: str | None = None
    children: tuple[Condition, ...] = ()


@dataclass(frozen=True)
class EdgeDef:
    src: str
    dst: str
    # Explicit data mapping: (source_output_port, destination_input_port) pairs.
    # An empty mapping is a pure control edge (ordering only, no data moved).
    mapping: tuple[tuple[str, str], ...] = ()
    when: Condition | None = None
    loop_max: int | None = None
    # Dynamic fan-out (G1). On a src->worker edge, ``fan_out`` names the source
    # ARRAY output port whose elements are mapped one-per-instance: the worker
    # node runs once per element (in parallel) and each of its output ports is
    # aggregated into an index-ordered list stored in the worker's own port.
    # On a worker->collector edge, ``fan_in="list"`` marks that the mapped source
    # port already holds that aggregated list (a load-time type-lift marker;
    # runtime treats the edge as a plain mapping).  See docs/fan-out.md.
    fan_out: str | None = None
    fan_in: Literal["list"] | None = None


@dataclass(frozen=True)
class WorkflowDef:
    name: str
    provider: str
    entry: str
    nodes: tuple[NodeDef, ...]
    edges: tuple[EdgeDef, ...]
    default_model: str = ""
    # Seed values exposed as the output ports of the reserved IN_NODE_ID source.
    # Values are type-native (str / int / float / bool / list / dict) to match the
    # structured wire format.
    initial_state: tuple[tuple[str, object], ...] = field(default=())
    # Optional JSON Schema per ``$in`` seed key (name -> schema).  OPT-IN: a state
    # key with no entry here stays untyped (edges out of it are exempt from type
    # checking, as before).  A key WITH a schema is type-checked at load time — its
    # consuming edges must match, and an array schema lets it drive a ``fan_out``.
    # This is the workflow's typed input signature; pairs mirror ``initial_state``.
    in_schema: tuple[tuple[str, dict[str, object]], ...] = field(default=())
    # Custom tool manifest: (tool_name, "module:attr") pairs.  Each ref is loaded
    # at run/generate time (like a script node's ``run``) and registered into the
    # tool registry under ``tool_name``, so agent nodes can name it in ``tools``.
    tool_refs: tuple[tuple[str, str], ...] = field(default=())
    max_concurrency: int = 0  # 0 (or negative) = unlimited (current behaviour)
    # Dynamic fan-out (G1) per-group concurrency cap: at most this many worker
    # instances of ONE fan-out node run at once (0/negative = unlimited).  This is
    # a DEDICATED limiter, independent of ``max_concurrency`` — the scheduler
    # semaphore must not be re-acquired inside a fan node (self-nesting would
    # deadlock at cap=1).  Both engines apply it identically (interpret == compile).
    fan_max_concurrency: int = 0


def entry_frontier(wf: WorkflowDef) -> tuple[str, ...]:
    """The nodes execution starts from.

    ``$in`` is the conceptual head: every node whose only non-loop predecessors
    are ``$in`` (or none) is an entry, so a workflow naturally has multiple
    parallel entries.  When ``wf.entry`` is explicitly set it takes precedence
    (a single named start), preserving the older single-entry behaviour.

    Returned in ``wf.nodes`` declaration order for deterministic scheduling.
    """
    if wf.entry:
        return (wf.entry,)
    has_real_pred: set[str] = set()
    for e in wf.edges:
        if e.loop_max is not None:
            continue  # loop back-edges don't gate the initial frontier
        if e.src == IN_NODE_ID or e.dst == OUT_NODE_ID:
            continue
        has_real_pred.add(e.dst)
    return tuple(n.id for n in wf.nodes if n.id not in has_real_pred)


def agent_is_structured(node: NodeDef) -> bool:
    """Whether an agent produces a structured result via ``submit_result``.

    An agent is plain-text when it has no output port, or exactly one scalar
    ``string`` port (its reply text goes verbatim into that port).  Otherwise it
    is structured: it must ``submit_result`` an object validated against the
    schema derived from its output ports.
    """
    ports = node.output_ports
    if len(ports) == 0:
        return False
    if len(ports) == 1 and ports[0].schema == {"type": "string"}:
        return False
    return True


def agent_output_schema(node: NodeDef) -> dict[str, object]:
    """The JSON Schema an agent's ``submit_result`` object is validated against.

    Derived from the output ports (no separately-authored schema):

    * multiple ports → an object with one property per port, all required;
    * a single structured port → that port's own schema (the whole submitted
      value is stored in it).

    Only meaningful when :func:`agent_is_structured` is True.
    """
    ports = node.output_ports
    if len(ports) == 1:
        return dict(ports[0].schema)
    return {
        "type": "object",
        "properties": {p.name: dict(p.schema) for p in ports},
        "required": [p.name for p in ports if p.required],
    }

