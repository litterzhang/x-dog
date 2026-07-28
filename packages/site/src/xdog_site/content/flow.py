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
    """A single capability with a one-line explanation, grouped by category."""

    title: str
    detail: str
    category: str = ""


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
    done: bool = False


@dataclass(frozen=True)
class Field:
    """One key in a JSON object schema: name, whether required, and its meaning."""

    name: str
    required: str  # "required" | "optional" | a default like "default \"\""
    detail: str


@dataclass(frozen=True)
class SchemaBlock:
    """A named group of JSON fields (e.g. the workflow object, a node object)."""

    title: str
    intro: str
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Command:
    """A CLI subcommand: its invocation, one-line purpose, and notable flags."""

    usage: str
    purpose: str
    flags: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Rule:
    """A single load-time validation rule (message + what triggers it)."""

    message: str
    trigger: str


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
        heading="One runtime container in, workflow outputs out",
        body=(
            "Two reserved synthetic nodes bracket every run. The workflow's state block is exposed as "
            "the output ports of a source node called $in, so the same graph runs with different "
            "inputs without editing the JSON. Nodes wire their output ports to a sink node called "
            "$output with ordinary edges, and those collected key/value pairs are the workflow's "
            "result — flushed the moment each feeding node finishes, so a looped writer's latest "
            "value wins.",
            "execute() returns a single runtime container: ctx (the last node's step/id/name), stack "
            "(a per-node delta trace — one {step, node, in, out} frame per execution, so a looped "
            "node's refinement history is visible), state (real-node outputs only), in ($in), and out "
            "($output). The CLI prints out by default, falling back to the full container.",
        ),
    ),
    Section(
        heading="Two ways to run: interpret or compile",
        body=(
            "The same JSON can be executed directly by the runtime, or compiled with codegen into a "
            "single self-contained Python module. The generated code keeps node outputs in the same "
            "nested port structure the interpreter uses and builds the identical runtime container, so "
            "the two forms agree node-for-node — and the emitted module passes the same ruff and mypy "
            "--strict gate as hand-written code.",
            "Linear and parallel graphs compile to BFS waves (a lone await, or asyncio.gather for a "
            "fan-out); bounded loops become a for-range; and a workflow with forward conditionals "
            "compiles to a topologically-ordered, guard-gated body instead. The interpreter's "
            "port-local prompt interpolation and source-node condition evaluation are reproduced "
            "exactly.",
        ),
    ),
    Section(
        heading="Typed ports and optional inputs",
        body=(
            "Every port carries a JSON type (string, integer, number, boolean, array, object). A "
            "script node sees its inputs coerced to Python values by that type and returns values "
            "coerced back to the string wire format; agent ports are almost always strings. An empty "
            "value coerces to the type's zero-value (0, 0.0, false, [], {}).",
            "An input port can be marked optional, which exempts it from the rule that every declared "
            "input must be fed by an edge. That is how a loop-carried value — absent on the first pass "
            "and supplied only by the back-edge — stays an internal port instead of leaking into the "
            "workflow's user-facing inputs.",
        ),
    ),
    Section(
        heading="Structured output and web search",
        body=(
            "An agent node can declare an output_schema: the engine adds a submit_result tool and a "
            "directive, and the validated JSON the agent submits becomes the node's output port — no "
            "brittle parsing of free-form text.",
            "An agent node can also enable a built-in web_search tool, optionally naming a distinct "
            "browsing model (some models don't browse, so a workflow can run the node on one model and "
            "search with another). Tools beyond the built-ins are declared in a JSON manifest of "
            "module:function references, loaded at both run and generate time.",
        ),
    ),
    Section(
        heading="Author visually, review as a diagram",
        body=(
            "An interactive terminal builder (xdog-flow build) edits the graph across a Builder page "
            "(with Graph / Nodes / Edges blocks), a Functions page that shows each script node's "
            "source, and a Tools page listing every built-in and custom tool. It round-trips JSON "
            "losslessly — parse then re-serialise is the identity — so hand-edited and TUI-edited "
            "files stay interchangeable.",
            "The same definition renders four ways: a plain-text listing, a layered box-drawing ASCII "
            "diagram with orthogonal edge routing and right-side lanes for skip/loop edges, a "
            "Graphviz-backed SVG (with a dependency-free fallback), and a Mermaid flowchart.",
        ),
    ),
    Section(
        heading="Deliberately single-machine — a kernel, not a platform",
        body=(
            "flow runs one graph in one asyncio event loop in one process: `pip install`, then "
            "`asyncio.run(execute(wf))` — no server, no queue, no database. That is a chosen boundary, "
            "not an unfinished one. Distributed execution is a non-goal: building it would mean "
            "rewriting the executor's core (a node is a closure over shared in-process state, not a "
            "serialisable task) and would force a trade against the interpret==compile guarantee that "
            "makes a workflow compilable to a self-contained module in the first place.",
            "When a run must scale across machines, embed flow as a LIBRARY — run a flow graph as one "
            "unit of work inside a durable engine like Temporal — rather than asking flow to become "
            "that engine. Keeping the kernel small, type-checked, and compilable is the value a "
            "heavyweight platform cannot offer; competing with Temporal on distribution would forfeit "
            "it. The resilience features flow does ship (retry, checkpoint/resume, isolation, "
            "human-in-the-loop, deterministic reuse) are all the single-machine, in-kernel kind.",
        ),
    ),
)


# --- Features ----------------------------------------------------------------

FEATURES: tuple[Feature, ...] = (
    # --- Modeling: how a workflow is shaped ---
    Feature("Node-private ports", "Typed inputs/outputs wired by explicit edge mappings — no shared flat state.", "Modeling"),
    Feature("Typed port coercion", "string / integer / number / boolean / array / object, with empty → zero-value.", "Modeling"),
    Feature("Optional inputs", "Mark an input optional so a loop-carried port need not be fed on the first pass.", "Modeling"),
    Feature("$in / $output nodes", "Reserved source + sink: state seeds $in; edges to $output collect the result.", "Modeling"),
    Feature("Conditional edges", "equals / contains / and / or / not guards over a source output port.", "Modeling"),
    Feature("Bounded loops", "Back-edges declare loop.max so revise cycles terminate; re-runs reset successors.", "Modeling"),
    Feature("Script nodes", "Inline code or a run: module:func reference, imported with the workflow dir on path.", "Modeling"),
    Feature("Validate before running", "Unknown ports, unfed inputs, ambiguous producers, unbounded loops fail fast.", "Modeling"),
    # --- Execution: how a run behaves ---
    Feature("Parallel executor", "Readiness-based fan-out/fan-in; every ready node runs concurrently via gather.", "Execution"),
    Feature("Concurrency caps", "max_concurrency bounds how many nodes run at once via a semaphore (default: unlimited).", "Execution"),
    Feature("Runtime container", "execute() returns {ctx, stack, state, in, out, failed, memo}: outputs plus a per-node trace.", "Execution"),
    Feature("Structured event stream", "on_event streams NodeStarted / NodeFinished / NodeFailed with per-node duration and tokens.", "Execution"),
    Feature("Metrics aggregation", "A MetricsCollector consumes the event stream into a per-node + per-run snapshot (runs, duration, tokens, failures).", "Execution"),
    Feature("Offline dry-run", "Run with no LLM calls; agent nodes echo DRYRUN:<model> to test wiring.", "Execution"),
    # --- Resilience: surviving failure & long runs ---
    Feature("Per-node retry", "A RetryPolicy(max, backoff) retries a failed node before giving up (default: fail-fast).", "Resilience"),
    Feature("Failure isolation", "on_error:isolate records a failed branch in runtime.failed and skips only its sub-tree.", "Resilience"),
    Feature("Checkpoint & resume", "A CheckpointStore persists progress by run id; a resumed run skips already-completed nodes.", "Resilience"),
    Feature("Human-in-the-loop", "A human node pauses awaiting a named signal; deliver it and resume the run to continue.", "Resilience"),
    Feature("Deterministic reuse", "deterministic:true memoises output by (node, input hash) for safe retry/resume.", "Resilience"),
    # --- Agents & tools ---
    Feature("Agent web search", "Agent nodes can enable a built-in web_search tool with its own browsing model.", "Agents & tools"),
    Feature("JSON-declared custom tools", "A module:func tool manifest, loaded at run and generate time.", "Agents & tools"),
    Feature("Structured output", "output_schema forces a submit_result call whose validated JSON is the port.", "Agents & tools"),
    # --- Codegen & authoring ---
    Feature("Code generation", "Compile a workflow JSON to a runnable, ruff- and mypy --strict-clean Python module.", "Codegen & authoring"),
    Feature("Interpret == compile", "The generated module mirrors the interpreter — every feature above works both ways.", "Codegen & authoring"),
    Feature("Interactive builder TUI", "xdog-flow build with Builder, Functions, and Tools pages; round-trips JSON.", "Codegen & authoring"),
    Feature("Four diagram renderers", "Text listing, layered ASCII, Graphviz SVG (with fallback), and Mermaid.", "Codegen & authoring"),
)

# Category display order for the Features page (any unlisted category falls to the end).
FEATURE_CATEGORIES: tuple[str, ...] = (
    "Modeling",
    "Execution",
    "Resilience",
    "Agents & tools",
    "Codegen & authoring",
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


# --- Positioning, non-goals, remaining gaps, and roadmap ---------------------

# The core positioning statement — what flow deliberately is, and is not.
POSITIONING: Section = Section(
    heading="What flow is — and what it is not",
    body=(
        "flow is a single-machine, type-safe, compilable LLM-workflow kernel. A workflow is one JSON "
        "graph that the runtime interprets directly OR compiles to a self-contained, dependency-free "
        "Python module — and the two paths agree node-for-node (interpret == compile). It runs with a "
        "plain `pip install` and `asyncio.run(execute(wf))`: no server, no queue, no database.",
        "flow is deliberately NOT a distributed orchestration platform. It does not span machines, own "
        "a worker pool, or replace Temporal / Step Functions / LangGraph Platform. When you need to "
        "scale a run across hosts, embed flow as a library — run a flow graph as one activity inside "
        "your durable engine — rather than asking flow to become that engine. Keeping the kernel small, "
        "readable, and compilable is the point; that is exactly what a heavyweight platform can't give.",
    ),
)

# Non-goals: capabilities we have deliberately decided NOT to build, with the reasoning.
NON_GOALS: tuple[Gap, ...] = (
    Gap("Distributed execution", "Not a goal. The executor is one asyncio event loop in one process; "
        "there is no cross-machine worker pool, queue, or per-graph scheduling — by design. Building it "
        "would mean rewriting the executor's core (nodes are closures over shared in-process state, not "
        "serialisable tasks) and would force a choice between distribution and the interpret==compile "
        "guarantee — flow's signature feature — while shedding its zero-ops, single-binary advantage. "
        "Cross-machine scale belongs to a durable engine flow is embedded in, not to flow itself."),
    Gap("Multi-tenancy & auth", "Not a goal. flow is a kernel/library, not a hosted service. Isolation, "
        "authn/authz, and quotas per tenant are the host environment's job (the reference HaveFun runner "
        "adds only a single-slot guard for safety)."),
    Gap("Built-in scheduling", "Not a goal. Cron/interval/event triggers are wired around flow by the "
        "host (e.g. a systemd timer driving a cycle), not baked into the engine — the same separation a "
        "library keeps from its scheduler."),
    Gap("Compensation / rollback", "Not a goal (as an engine feature). Saga-style compensation earns its "
        "keep in distributed, long-running, cross-service flows — which flow deliberately isn't. For the "
        "engine to undo a side-effect it would have to track side-effects and their inverses, colliding "
        "with the node = pure input-ports → output-ports model and the interpret==compile guarantee. "
        "Failure cleanup is instead expressible with existing primitives — an on_error:isolate node "
        "collects failures into runtime.failed, and a downstream cleanup node reads it — or handled in "
        "the host/driver layer, exactly as the auto-enrich driver rolls back with `git checkout` outside "
        "the engine."),
    Gap("External telemetry export", "Not a goal (as a core feature). Metrics are aggregated in-kernel by "
        "MetricsCollector; pushing them to OpenTelemetry / Prometheus / a trace backend is left to the "
        "caller, who consumes the P3 event stream (or the metrics snapshot) and forwards it. Baking an "
        "exporter in would add third-party deps to a kernel whose whole point is to have none — so it "
        "stays outside, wired up by the host if wanted."),
)

GAPS: tuple[Gap, ...] = (
    Gap("Cost budgets & quotas", "Token usage is reported per node (P3) and aggregated per run (metrics), "
        "but there is no enforced per-run cost ceiling that aborts a run before it overspends. A real, "
        "in-kernel gap — a small execute() guard, no distribution required."),
)

ROADMAP: tuple[Phase, ...] = (
    Phase("P1", "Per-node retry & timeout policy", (
        "Done: NodeDef carries a RetryPolicy(max, backoff); the executor retries a "
        "failed node (script or agent) up to max times with a per-attempt backoff, "
        "then re-raises — preserving fail-fast when retries are exhausted.",
        "Removes the 'one LLM hiccup fails the whole run' failure mode.",
        "Notably, this was implemented by flow itself: the tools/autoenrich workflow "
        "(build → gate → validate, with a bounded fix loop) wrote and self-reviewed it.",
    ), done=True),
    Phase("P2", "Checkpoint & resume", (
        "Done: a CheckpointStore protocol (with a JSONFileCheckpointStore) persists "
        "a run's progress snapshot keyed by a run id; the executor saves after each "
        "node and, on restart with the same run id, restores and skips already-"
        "completed nodes instead of re-running them.",
        "Turns long agent runs from all-or-nothing into recoverable. Codegen honours "
        "it too via FLOW_RUN_ID / FLOW_CHECKPOINT_DIR, so both run paths agree.",
        "Also implemented by the tools/autoenrich workflow itself.",
    ), done=True),
    Phase("P3", "Structured event stream", (
        "Done: typed NodeStarted / NodeFinished / NodeFailed events carry per-node "
        "wall-clock duration and (for agent nodes) token usage; the executor "
        "delivers them via an on_event callback, and the generated module logs the "
        "same lifecycle to the flow.generated.events logger.",
        "The foundation for observability and live TUI/web progress. Also "
        "implemented by the tools/autoenrich workflow itself.",
    ), done=True),
    Phase("P4.1", "Per-branch failure isolation", (
        "Done: a node marked on_error:\"isolate\" whose branch fails is captured "
        "(its error recorded in runtime[\"failed\"]) and its downstream sub-tree "
        "skipped, while independent sibling branches still run to completion. The "
        "default on_error:\"fail\" keeps the fail-fast semantics.",
        "Implemented by the tools/autoenrich workflow itself; codegen mirrors it.",
    ), done=True),
    Phase("P4.2", "Concurrency caps", (
        "Done: a workflow (or execute() override) can cap how many nodes run at "
        "once via an asyncio.Semaphore, so a wide fan-out can't burst past a "
        "provider's rate limits. Unlimited by default.",
        "Implemented by the tools/autoenrich workflow itself; codegen mirrors it.",
    ), done=True),
    Phase("P4.3", "Human-in-the-loop", (
        "Done: a human node awaits a named signal — absent, the run checkpoints and "
        "raises WorkflowPaused (the generated module exits with a PAUSED line); "
        "deliver the signal and resume with the same run id to pass the gate. Real "
        "stop-for-approval built on P2 checkpointing.",
        "Implemented by the tools/autoenrich workflow itself; codegen mirrors it.",
    ), done=True),
    Phase("P4.4", "Deterministic nodes (safe retries)", (
        "Done: a node marked deterministic:true memoises its output keyed by "
        "(node id, input hash), so a retry or resume with the same input reuses the "
        "result instead of repeating a side-effect. Non-deterministic nodes (the "
        "default) always run.",
        "Implemented by the tools/autoenrich workflow itself; codegen mirrors it.",
    ), done=True),
)


# --- Reference: the workflow JSON schema -------------------------------------

SCHEMA_BLOCKS: tuple[SchemaBlock, ...] = (
    SchemaBlock(
        title="Workflow (top level)",
        intro="A workflow is one JSON object. Only entry is effectively required — a run needs a "
        "first node — but a useful workflow also declares nodes and edges.",
        fields=(
            Field("name", 'default ""', "Human-readable workflow name; shown in diagrams and the runtime container."),
            Field("provider", 'default ""', "LLM provider id used to build the default agent stream (e.g. copilot)."),
            Field("entry", "required", "Id of the first node to run. Must name a real node."),
            Field("defaults.model", 'default ""', "Fallback model for any agent node that does not set its own model."),
            Field("state", "default {}", "Object of initial values, exposed as the output ports of the $in source."),
            Field("nodes", "default []", "The list of node objects (see below)."),
            Field("edges", "default []", "The list of edge objects wiring node ports together."),
            Field("tools", "default {}", "Custom-tool manifest: {tool_name: \"module.path:callable\"}."),
        ),
    ),
    SchemaBlock(
        title="Node",
        intro="A node is either an agent (calls an LLM) or a script (runs Python). type defaults to "
        "agent. An agent node must not set code or run; a script node must set exactly one of them.",
        fields=(
            Field("id", "required", "Unique node id. $in and $output are reserved and rejected."),
            Field("type", 'default "agent"', '"agent" or "script".'),
            Field("model", "optional", "Agent only: overrides defaults.model for this node."),
            Field("system_prompt", 'default ""', "Agent only: system prompt; {{port}} reads this node's inputs."),
            Field("prompt", 'default ""', "Agent only: user prompt; {{port}} is port-local to this node's inputs."),
            Field("tools", "default []", "Agent only: names of built-in or manifest tools to expose."),
            Field("web_search", "default false", "Agent only: enable the built-in web_search tool."),
            Field("web_search_model", "optional", "Agent only: a distinct browsing model for web_search."),
            Field("output_schema", "default {}", "Agent only: {field: jsontype} — forces a submit_result call."),
            Field("code", "optional", "Script only: inline source defining exactly one ctx-first function."),
            Field("run", "optional", 'Script only: a "module.path:callable" reference imported at run time.'),
            Field("inputs", "default []", "Input ports (bare name or {name, type, optional})."),
            Field("outputs / output", "default []", "Output ports; output is singular sugar for one port."),
        ),
    ),
    SchemaBlock(
        title="Port",
        intro="A port is a bare string (a string-typed port) or an object. Its type drives coercion "
        "between the string wire format and the Python value a script sees.",
        fields=(
            Field("name", "required", "Port name; referenced by edge maps and {{name}} interpolation."),
            Field("type", 'default "string"', "One of string, integer, number, boolean, array, object."),
            Field("optional", "default false", "Input only: exempt from the must-be-fed rule (loop-carried values)."),
        ),
    ),
    SchemaBlock(
        title="Edge",
        intro="An edge moves data from a source node's output ports to a destination node's input "
        "ports. An empty map is a pure control edge (ordering only). $in is source-only; $output is "
        "sink-only.",
        fields=(
            Field("from", "required", "Source node id, or the reserved $in source."),
            Field("to", "required", "Destination node id, or the reserved $output sink."),
            Field("map", "default {}", "{source_output_port: destination_input_port} pairs."),
            Field("when", "optional", "A condition; the edge only fires (or feeds) when it holds."),
            Field("loop.max", "optional", "Marks a bounded back-edge; required when to is not strictly after from."),
        ),
    ),
)

# The type system (flow.coerce.VALID_TYPES) — how a stored string becomes a Python value.
TYPE_ROWS: tuple[tuple[str, str, str], ...] = (
    ("string", "the string unchanged", '""'),
    ("integer", "int(value)", "0"),
    ("number", "float(value)", "0.0"),
    ("boolean", "true/1/yes/on → true; false/0/no/off/'' → false", "false"),
    ("array", "json.loads, must be a JSON array", "[]"),
    ("object", "json.loads, must be a JSON object", "{}"),
)

# Condition operators (flow.conditions.evaluate). value/text support {{interpolation}}.
CONDITION_ROWS: tuple[tuple[str, str, str], ...] = (
    ("equals", '{"equals": {"value": V, "text": T}}', "interpolate(value) == interpolate(text)"),
    ("contains", '{"contains": {"value": V, "text": T}}', "interpolate(text) in interpolate(value)"),
    ("not", '{"not": <cond>}', "negation of one child condition"),
    ("and", '{"and": [<cond>, ...]}', "all children hold"),
    ("or", '{"or": [<cond>, ...]}', "any child holds"),
)

# The runtime container returned by execute() (ExecResult.runtime).
RUNTIME_ROWS: tuple[tuple[str, str], ...] = (
    ("ctx", "The last node to run: {step, node_id, workflow_name}. This is also what a script node receives."),
    ("stack", "A per-node delta trace: one {step, node, in, out} frame per run; a looped node appears once per pass."),
    ("state", "Real-node outputs only: {node_id: {port: value}} — excludes $in and $output."),
    ("in", "The $in seed: the workflow's state, with any run-time input overrides applied."),
    ("out", "The $output map: the key/value pairs collected from edges targeting $output."),
)

# The xdog-flow CLI (flow.cli). Every subcommand accepts a .json or .svg (with embedded JSON) config.
COMMANDS: tuple[Command, ...] = (
    Command("xdog-flow validate <config>", "Load and validate a workflow; prints OK or the first error."),
    Command(
        "xdog-flow run <config>",
        "Execute a workflow and print its $output (or the whole runtime container when none is declared).",
        (
            ("--dry-run", "Inject a stub LLM; agent nodes echo DRYRUN:<model> so you can test wiring offline."),
            ("--input K=V", "Seed or override a $in value (repeatable; split on the first =)."),
            ("--provider X", "Override the AI provider."),
            ("--timeout N", "Per-node wall-clock timeout in seconds (default 120)."),
            ("-v / --verbose", "Show flow's DEBUG logs — node execution and loop firing."),
        ),
    ),
    Command(
        "xdog-flow generate <config>",
        "Compile the workflow to a standalone Python module.",
        (("-o / --output FILE", "Write to a file instead of stdout."),),
    ),
    Command(
        "xdog-flow graph <config>",
        "Render the workflow graph.",
        (
            ("--mermaid", "Emit a Mermaid flowchart."),
            ("--svg", "Emit an SVG document with the workflow JSON embedded."),
        ),
    ),
    Command("xdog-flow build <config>", "Open the interactive TUI builder (created if the file is missing)."),
)

# The complete set of load-time checks (flow.loader.validate_workflow). Every one raises
# WorkflowValidationError before a single node runs — the "validated before it runs" guarantee.
VALIDATION_RULES: tuple[Rule, ...] = (
    Rule("Node id must be non-empty", "A node object has an empty id."),
    Rule("Node id '$in' / '$output' is reserved", "A real node tries to claim a reserved id."),
    Rule("Duplicate node ids", "Two nodes share the same id."),
    Rule("Entry node not found in nodes", "entry names a node that does not exist."),
    Rule("Agent node must not set 'run' / 'code'", "An agent node carries script-only fields."),
    Rule("Script must set exactly one of 'code' or 'run'", "A script node sets both or neither."),
    Rule("Script 'run' must match module.path:callable", "A malformed run reference."),
    Rule("Script inline code invalid / must define one function", "Inline code fails to parse or has ≠1 function."),
    Rule("Script function's first parameter must be 'ctx'", "The inline function signature does not start with ctx."),
    Rule("Script params != declared inputs", "The function parameters and the declared input ports disagree."),
    Rule("References unknown tool", "A node names a tool that is neither a built-in nor in the manifest."),
    Rule("Tool manifest ref must match module.path:callable", "A malformed custom-tool reference."),
    Rule("Edge src '$output' / dst '$in' not allowed", "Using the sink as a source, or the source as a sink."),
    Rule("Edge src / dst not found in nodes", "An edge names an endpoint that does not exist."),
    Rule("Source/destination has no such port", "An edge map names a port a node doesn't declare."),
    Rule("Back-edge must have loop.max >= 1", "A back-edge (dst not strictly after src) is not a bounded loop."),
    Rule("Input port is fed by N unconditional edges", "Two always-on producers target one input (ambiguous)."),
    Rule("Input port is not fed by any edge mapping", "A required (non-optional) input port has no feeder."),
)
