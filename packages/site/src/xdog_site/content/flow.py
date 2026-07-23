"""Authored content for the /packages/flow deep-dive sub-pages.

Structured (frozen dataclasses) so templates stay logic-free and the content is
unit-testable, mirroring :mod:`xdog_site.content.packages`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    """A titled block of prose lines and optional bullet points."""

    heading: str
    body: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Feature:
    """A single capability with a one-line explanation."""

    title: str
    detail: str


@dataclass(frozen=True)
class Example:
    """A showcased workflow: an example file stem plus authored commentary."""

    stem: str  # matches packages/flow/examples/<stem>.json
    title: str
    blurb: str
    effect: str  # what running it produces


@dataclass(frozen=True)
class Gap:
    """A capability gap vs production-grade orchestrators."""

    area: str
    detail: str


@dataclass(frozen=True)
class Phase:
    """A roadmap phase."""

    tag: str
    title: str
    items: tuple[str, ...]


# --- Design ------------------------------------------------------------------

DESIGN_SECTIONS: tuple[Section, ...] = (
    Section(
        heading="Node-private ports, not shared state",
        body=(
            "A flow workflow is a graph of nodes connected by edges. Data does not travel through a "
            "shared global dict — each node declares typed input and output ports, and each edge "
            "carries an explicit mapping that says which source output port feeds which destination "
            "input port.",
            "Because the wiring is spelled out rather than implied by matching key names, the graph "
            "can be validated before it runs: unknown ports, two producers feeding one input, or an "
            "unfed required input all fail fast at load time.",
        ),
    ),
    Section(
        heading="Readiness-based parallel executor",
        body=(
            "The executor runs nodes concurrently by readiness: a node becomes ready when all of its "
            "non-loop predecessors have completed, and every currently-ready node is launched at once. "
            "A fan-in node simply waits until all of its upstreams finish.",
            "Linear pipelines behave exactly like a sequential run; diamonds and fan-outs get "
            "parallelism for free.",
        ),
    ),
    Section(
        heading="Conditional and bounded-loop edges",
        body=(
            "Edges can carry a condition (equals / contains / and / or / not over a source output "
            "port) so branches only fire when their guard holds. A back-edge must declare a bounded "
            "loop (loop.max), which is how a review→revise cycle stays finite.",
        ),
    ),
    Section(
        heading="Two ways to run: interpret or compile",
        body=(
            "The same JSON can be executed directly by the runtime, or compiled with codegen into a "
            "single self-contained Python module. The generated code keeps node outputs in the same "
            "nested port structure the interpreter uses, so the two forms agree, and it passes the "
            "same ruff and mypy --strict gate as hand-written code.",
        ),
    ),
    Section(
        heading="Author visually, review as a diagram",
        body=(
            "An interactive terminal builder (xdog-flow build) edits the graph, shows each script "
            "node's source, and lists every built-in and custom tool. Deterministic ASCII diagrams "
            "and Graphviz-backed SVG render the same definition for review.",
        ),
    ),
)


# --- Features ----------------------------------------------------------------

FEATURES: tuple[Feature, ...] = (
    Feature("Node-private ports", "Typed inputs/outputs wired by explicit edge mappings — no shared flat state."),
    Feature("Parallel executor", "Readiness-based fan-out/fan-in; ready nodes run concurrently."),
    Feature("Conditional edges", "equals / contains / and / or / not guards over a source output port."),
    Feature("Bounded loops", "Back-edges declare loop.max so revise cycles terminate."),
    Feature("Code generation", "Compile a workflow JSON to a runnable, type-checked Python module."),
    Feature("Agent web search", "Agent nodes can enable a built-in web_search tool with its own model."),
    Feature("JSON-declared custom tools", "A module:func tool manifest, loaded at run and generate time."),
    Feature("Structured output", "output_schema forces a submit_result call whose validated JSON is the port."),
    Feature("Script nodes", "Inline code or a run: module:func reference, imported with the workflow dir on path."),
    Feature("Interactive builder TUI", "xdog-flow build with graph, Functions, and Tools pages."),
    Feature("ASCII + SVG diagrams", "Deterministic ASCII rendering and Graphviz-backed SVG of any workflow."),
    Feature("Offline dry-run", "Run with no LLM calls; agent nodes echo DRYRUN:<model> to test wiring."),
)


# --- Examples (the shipped packages/flow/examples/*.json) --------------------

EXAMPLES: tuple[Example, ...] = (
    Example(
        stem="agent_calculator",
        title="Agent Calculator (script → agent + bash)",
        blurb="Two nodes: make_problem (a script node) turns the typed integer inputs a and b into an "
        "arithmetic string like \"347 + 895\"; solve (an agent node with the bash tool) is told not to do "
        "the math in its head — it shells out to compute the expression and replies with the integer.",
        effect="make_problem builds the expression from the inputs, then solve runs a bash command and "
        "returns the answer (e.g. a=12, b=30 → answer \"42\"). A dry-run only exercises the wiring; a real "
        "run has the agent actually compute via bash.",
    ),
    Example(
        stem="refine_loop",
        title="Generator ↔ Critic (bounded refine loop with web search)",
        blurb="Two agents in a feedback loop: draft writes a concise answer to a topic; critic fact-checks "
        "it with the web_search tool and replies APPROVE or REVISE + notes. A bounded loop edge "
        "(critic→draft, when the feedback contains REVISE, loop≤2) sends the notes back so draft can "
        "improve the answer.",
        effect="draft produces an answer, critic web-searches to verify it; if it says REVISE the answer is "
        "rewritten and re-checked, up to twice, before the loop settles on an APPROVEd answer. This is the "
        "canonical generate-and-critique multi-agent pattern.",
    ),
)


# --- Gaps vs production-grade + roadmap --------------------------------------

GAPS: tuple[Gap, ...] = (
    Gap("Retry & backoff", "No built-in per-node retry or exponential backoff — a node failure fails the "
        "run. Resilience is currently added around the engine (e.g. re-running a whole cycle)."),
    Gap("Checkpoint & resume", "State is an in-memory dict; there is no checkpointing or durable store, so "
        "a crash mid-run loses progress and cannot resume from the last completed node."),
    Gap("Failure isolation", "A fan-out uses asyncio.gather, which is fail-fast: one failing branch cancels "
        "its siblings. There is no per-branch isolation or compensation."),
    Gap("Observability", "No structured event stream, metrics, or tracing spans — only logging. There is no "
        "run timeline or per-node token/latency accounting out of the box."),
    Gap("Concurrency limits", "The ready set launches with no semaphore or worker-pool cap, so a very wide "
        "graph can burst well past provider rate limits."),
    Gap("Human-in-the-loop", "No first-class pause/await-signal so a run can stop for approval and resume."),
    Gap("Idempotency", "No exactly-once guarantees; a retried node with side effects could repeat them. The "
        "reference daemon adds its own deterministic commit logic instead."),
)

ROADMAP: tuple[Phase, ...] = (
    Phase("P1", "Per-node retry & timeout policy", (
        "A RetryPolicy(max, backoff) on NodeDef, applied by the executor.",
        "Directly removes the 'one LLM hiccup fails the whole run' failure mode.",
        "Smallest, lowest-risk increment — same style as the web_search / custom-tool additions.",
    )),
    Phase("P2", "Checkpoint & resume", (
        "Serialise the nested outputs store to a pluggable backend keyed by a run id.",
        "Resume from the last completed node after a crash.",
        "Turns long agent runs from all-or-nothing into recoverable.",
    )),
    Phase("P3", "Structured event stream", (
        "Emit NodeStarted / NodeFinished / NodeFailed events with duration and token usage.",
        "The foundation for observability and live TUI/web progress.",
    )),
    Phase("P4", "Durability & human-in-the-loop", (
        "Per-branch failure isolation and compensation.",
        "Concurrency caps (semaphore / worker pool) to respect provider limits.",
        "Pause/await-signal nodes for approvals; idempotency keys for safe retries.",
    )),
)
