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

import hashlib
import json
from dataclasses import asdict, dataclass, field
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


@dataclass(frozen=True)
class Port:
    """A named, JSON-Schema-typed data port on a node.

    ``schema`` is a JSON Schema fragment describing the port's value: a scalar
    ``{"type": "integer"}``, a structured ``{"type": "object", "properties":
    {...}, "required": [...]}``, or ``{"type": "array", "items": {...}}``.  The
    six top-level type names (``string``/``integer``/``number``/``boolean``/
    ``array``/``object``) drive wire-format coercion; nested structure is
    validated (not re-coerced) by fastjsonschema.

    ``required`` marks an **input** port that must be fed by an edge (the default).
    A non-required loop-carried value may be absent on the first pass; interpolation
    then yields ``""`` and scripts see the type's zero-value. Ignored on outputs.

    Bare string ports use the default string schema. Typed/structured ports pass a
    JSON Schema explicitly through ``schema``. ``type`` is a convenience view of
    ``schema["type"]``.
    """

    name: str
    schema: dict[str, object] = field(default_factory=lambda: {"type": "string"})
    required: bool = True

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
class InheritSpec:
    """Which node's agent session this node starts from.

    Declared rather than ambient: flow's promise is that data moves only along
    edges the graph can see, and a session crossing a node boundary is data. A
    reference the validator can check keeps that promise; a hidden channel would
    not.

    ``from_node`` may name the node itself, which is how a loop keeps its own
    context across iterations instead of restarting cold each pass.
    """

    from_node: str


@dataclass(frozen=True)
class NodeDef:
    id: str
    type: Literal["agent", "script", "human", "subflow"] = "agent"
    signal: str = ""
    model: str | None = None
    system_prompt: str = ""
    prompt: str = ""
    tools: tuple[str, ...] = ()

    # Skills whose instructions are prepended to this node's system prompt.
    # Resolved from *installed packages only*, never from the machine's own
    # skill directories: a workflow is a shareable artifact, and one that picked
    # up whatever happened to be on disk would behave differently for two people
    # with no way to tell from the file.
    skills: tuple[str, ...] = ()
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
    # CLI agent backend (docs/cli-agent.md): when set (e.g. "claude-cli" /
    # "codex-cli"), this agent node runs by shelling out to that CLI instead of the
    # in-process SDK.  A CLI agent node needs no provider (the CLI owns auth).
    backend: str | None = None
    # CLI agent tool allow-list: names the node may call (CLI built-ins like
    # "Read"/"Bash" or MCP tools "mcp__<server>__<tool>").  Empty = no tools.
    # flow passes these through to the CLI's own allow-list flag; it defines nothing.
    allowed_tools: tuple[str, ...] = ()
    # CLI agent MCP servers: (name, opaque-spec) pairs the node provides to its CLI.
    # The spec dict is passed through unparsed — flow format-converts it into the
    # CLI's MCP config (with ${ENV} secret interpolation), never validating fields,
    # so a CLI adding MCP config fields needs no flow change.  See cli-agent.md §5.1.
    mcp_servers: tuple[tuple[str, dict[str, object]], ...] = ()
    # Start this agent node from another agent node's session (its messages and
    # system prompt), overridable field by field.  Kept after the pre-existing
    # fields to preserve positional constructor calls.
    inherit: InheritSpec | None = None

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
    # On a worker->collector edge, ``fan_in`` marks the mapped source port as the
    # fan-out worker's aggregated result: ``"list"`` collects one value per instance
    # into an index-ordered list; ``"concat"`` flattens each instance's array-valued
    # port one level into a single flat list.  See docs/fan-out.md.
    fan_out: str | None = None
    fan_in: Literal["list", "concat"] | None = None
    # ``while`` loop sugar (docs/expressiveness.md B2).  A back-edge authored as
    # ``"while": <cond>`` desugars to ``when=<cond>`` + ``loop_max=<safe bound>`` with
    # this flag set.  The difference from a plain ``loop.max`` is the exhaustion
    # semantics: a strict loop that reaches its bound with the condition still true
    # is a non-convergence error; a plain ``loop.max`` silently stops.
    # Kept after the pre-existing fields to preserve positional constructor calls.
    loop_strict: bool = False


@dataclass(frozen=True)
class ScheduleDef:
    """How a workflow fires on its own (docs/scheduling.md).

    Declarative config for ``xdog-flow scheduling install`` — the engine never reads it; a
    scheduler (systemd timer / listener) wraps the built bundle.  Two modes:

    * ``mode="timer"`` — fire on a schedule: exactly one of ``every`` ("30s"/"15m"/
      "2h"/"1d") or ``cron`` (a 5-field cron expression).
    * ``mode="hook"`` — fire when an external event delivers ``signal`` via a
      ``listen`` transport (an opaque dict: ``{"type": "http"|"file"|"socket", ...}``).

    ``inputs`` is optional per-firing ``$in`` seed data (env ``FLOW_INPUTS``).

    ``timeout`` bounds one firing. It is not a nicety: a ``Type=oneshot`` unit
    inherits systemd's ``DefaultTimeoutStartSec`` (90s on most distributions),
    which would kill essentially any agentic workflow mid-run, so a scheduled
    workflow always gets an explicit bound (:data:`DEFAULT_SCHEDULE_TIMEOUT` when
    unset).  ``jitter`` spreads firings across a window so several workflows
    sharing an hour boundary do not start at the same instant.
    """

    mode: Literal["timer", "hook"]
    every: str | None = None
    cron: str | None = None
    inputs: tuple[tuple[str, object], ...] = ()
    signal: str | None = None
    listen: dict[str, object] | None = None
    timeout: str | None = None
    jitter: str | None = None


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
    # Optional scheduling (docs/scheduling.md): how the workflow fires on its own.
    # None = run-once (current behaviour).  Read only by ``xdog-flow scheduling install`` — the
    # engine ignores it (a scheduler wraps the built bundle, unchanged execution).
    schedule: ScheduleDef | None = None


def _edge_fingerprint(edge: EdgeDef) -> str:
    """Canonical hash input for an edge's authored semantics."""
    payload = json.dumps(asdict(edge), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _resolve_edge_ids(fingerprints: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve five-hex short hashes, suffixing only colliding bucket members."""
    buckets: dict[str, list[tuple[str, int, int]]] = {}
    occurrences: dict[str, int] = {}
    for index, fingerprint in enumerate(fingerprints):
        occurrence = occurrences.get(fingerprint, 0)
        occurrences[fingerprint] = occurrence + 1
        short = fingerprint[:5]
        buckets.setdefault(short, []).append((fingerprint, occurrence, index))

    resolved = [""] * len(fingerprints)
    for short, members in buckets.items():
        for collision, (_fingerprint, _occurrence, index) in enumerate(sorted(members)):
            suffix = "" if collision == 0 else f"-{collision}"
            resolved[index] = f"edge-{short}{suffix}"
    return tuple(resolved)


def edge_identities(wf: WorkflowDef) -> tuple[str, ...]:
    """Internal five-hex content hashes aligned with ``wf.edges``.

    IDs are not part of workflow JSON or :class:`EdgeDef`; collisions and exact
    duplicate edges receive deterministic numeric suffixes.
    """
    return _resolve_edge_ids(tuple(_edge_fingerprint(edge) for edge in wf.edges))


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


def agent_submits_object(node: NodeDef) -> bool:
    """Whether an agent's returned value is a *port-keyed object* rather than one value.

    The distinction between "submits an object" and "is structured" is easy to blur
    but they are not the same: a single structured port (say one ``{"type":
    "object"}`` output) is structured, yet the whole submitted value still goes
    verbatim into that one port.  Only a multi-port agent returns a dict that has to
    be split across ports.

    This is the single definition of that rule.  The executor uses it to store and
    to project fan instances, and :mod:`flow.testing` uses it to shape a stub into
    the same value a provider would have returned — so a stub cannot drift from
    production behaviour.
    """
    return agent_is_structured(node) and len(node.output_ports) > 1


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

