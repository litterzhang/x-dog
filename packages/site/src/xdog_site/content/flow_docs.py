"""Dynamic (Features + Roadmap) content for the ``flow`` package.

flow's static pages (Overview / Design / Reference / Examples) are markdown under
``content/pages/flow/``; its Features and Roadmap stay in Python here so flow
shares the same ``PackageDocs`` mechanism as the other packages. The feature and
phase data is migrated verbatim from the retired ``content/flow.py``.
"""

from __future__ import annotations

from xdog_site.content.docs import Feature, PackageDocs, Phase

_FEATURES = (
    # --- Modeling: how a workflow is shaped ---
    Feature("Node-private ports", "Typed inputs/outputs wired by explicit edge mappings — no shared flat state.", "Modeling"),
    Feature("JSON Schema ports", "A port declares a JSON Schema (scalar or nested object/array); one `required` flag replaces the old optional.", "Modeling"),
    Feature("Type-native wire", "Port values are live Python (int/float/bool/list/dict), not stringified — structure flows between nodes intact.", "Modeling"),
    Feature("$in / $output nodes", "Reserved source + sink: state seeds $in; edges to $output collect the result. `entry` is optional (derived from $in).", "Modeling"),
    Feature("Conditional edges", "equals / contains / and / or / not guards over a source output port.", "Modeling"),
    Feature("Bounded loops + cycle detection", "Back-edges declare loop.max so cycles terminate; an unbounded cycle fails validation with its path.", "Modeling"),
    Feature("Script nodes", "Inline code or a run: module:func reference, imported with the workflow dir on path.", "Modeling"),
    Feature("Edge type checking", "An edge requires the source and destination port types to match — including a sub-field's type via its schema.", "Modeling"),
    Feature("Validate before running", "Unknown ports, unfed inputs, ambiguous producers, unbounded cycles, port-type mismatches all fail fast.", "Modeling"),
    # --- Data flow: interpolation & mapping ---
    Feature("JSONPath interpolation", "A prompt reads a field with `{{ $.plan.tasks[0] }}`; both engines share one jsonpath-ng evaluator.", "Data flow"),
    Feature("Sub-field edge mapping", "An edge maps a nested field: `\"map\": {\"$.verdict.within_budget\": \"flag\"}`, type-checked against the source schema.", "Data flow"),
    Feature("Structured $in seed", "Initial state carries type-native JSON; a structured seed stays a dict/list, not a Python repr.", "Data flow"),
    # --- Execution: how a run behaves ---
    Feature("Parallel executor", "Readiness-based fan-out/fan-in; every ready node runs concurrently via gather. Multi-entry from the $in frontier.", "Execution"),
    Feature("Pure node + driver", "A node is a pure function (provider, ctx, inputs → outputs); a generic driver owns guards, retry, store, memo, budget, checkpoint.", "Execution"),
    Feature("Concurrency caps", "max_concurrency bounds how many nodes run at once via a semaphore (default: unlimited).", "Execution"),
    Feature("Runtime container", "execute() returns {ctx, stack, state, in, out, failed, memo, tokens_used}: outputs plus a per-node trace.", "Execution"),
    Feature("Structured event stream", "on_event streams NodeStarted / NodeFinished / NodeFailed with per-node duration and tokens.", "Execution"),
    Feature("Metrics aggregation", "A MetricsCollector consumes the event stream into a per-node + per-run snapshot (runs, duration, tokens, failures).", "Execution"),
    Feature("Cost budget", "execute(max_tokens=N) aborts a run with WorkflowBudgetExceeded once cumulative agent tokens pass the ceiling.", "Execution"),
    Feature("Offline dry-run", "Run with no LLM calls; agent nodes echo DRYRUN:<model> to test wiring.", "Execution"),
    # --- Resilience: surviving failure & long runs ---
    Feature("Per-node retry", "A RetryPolicy(max, backoff) retries a failed node before giving up (default: fail-fast). Retry lives in the driver.", "Resilience"),
    Feature("Failure isolation", "on_error:isolate records a failed branch in runtime.failed and skips only its sub-tree.", "Resilience"),
    Feature("Checkpoint & resume", "A CheckpointStore persists progress by run id; a resumed run skips already-completed nodes.", "Resilience"),
    Feature("Human-in-the-loop", "A human node pauses awaiting a named signal; deliver it and resume the run to continue.", "Resilience"),
    Feature("Deterministic reuse", "deterministic:true memoises output by (node, input hash) for safe retry/resume.", "Resilience"),
    # --- Agents & tools ---
    Feature("Agent multi-output ports", "An agent fans its submit_result object across several typed output ports by field name — each wired independently.", "Agents & tools"),
    Feature("Derived output schema", "An agent's structured-output contract is derived from its output ports (no separate output_schema), validated by fastjsonschema.", "Agents & tools"),
    Feature("Agent web search", "Agent nodes can enable a built-in web_search tool with its own browsing model.", "Agents & tools"),
    Feature("JSON-declared custom tools", "A module:func tool manifest, loaded at run and generate time.", "Agents & tools"),
    # --- Codegen & authoring ---
    Feature("Code generation", "Compile a workflow JSON to a runnable, ruff-clean Python module that mirrors the interpreter node-for-node.", "Codegen & authoring"),
    Feature("Interpret == compile", "Interpreter and generated module agree node-for-node — enforced by a cross-engine parity suite on every feature.", "Codegen & authoring"),
    Feature("Portable bundle", "generate --portable emits a self-contained dir (vendored ai/agent, pinned deps); --offline downloads wheels for a no-network install.", "Codegen & authoring"),
    Feature("Runtime overrides", "The generated module honours FLOW_INPUTS (JSON merged into $in) and FLOW_PROVIDER — parity with the interpreter's --input / --provider.", "Codegen & authoring"),
    Feature("Interactive builder TUI", "xdog-flow build with Builder, Functions, and Tools pages; round-trips JSON.", "Codegen & authoring"),
    Feature("Four diagram renderers", "Text listing, layered ASCII, Graphviz SVG (with fallback), and Mermaid.", "Codegen & authoring"),
)

_FEATURE_CATEGORIES = (
    "Modeling",
    "Data flow",
    "Execution",
    "Resilience",
    "Agents & tools",
    "Codegen & authoring",
)

_ROADMAP = (
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
    Phase("P5", "Expressiveness — structured data & JSON Schema", (
        "Done: the wire format is type-native — port values are live int/float/bool/"
        "list/dict, not stringified — so structure flows between nodes intact and an "
        "agent's structured result stays a real object.",
        "Done: ports carry a JSON Schema (scalar or nested), with one `required` flag "
        "replacing the old `optional`; an agent's structured-output contract is derived "
        "from its output ports (no separate output_schema) and validated by "
        "fastjsonschema; an agent can fan its result across several typed output ports.",
        "Done: interpolation, conditions and edge maps use JSONPath (`{{ $.plan."
        "tasks[0] }}`, `\"$.verdict.within_budget\"`) via one shared jsonpath-ng "
        "evaluator; edges are type-checked end to end, including a sub-field's type.",
        "Done: real cycle detection (an unbounded cycle fails validation with its "
        "path); `entry` is optional (derived from the $in frontier); the generated "
        "module honours FLOW_INPUTS / FLOW_PROVIDER overrides.",
        "Every item shipped with interpreter + codegen + a cross-engine parity test, so "
        "interpret == compile holds throughout. See docs/expressiveness.md.",
    ), done=True),
    Phase("P6", "Expressiveness — what still isn't expressible", (
        "Planned: numeric condition ops (gt/gte/lt/lte) so a loop can branch on a "
        "score, not just a string match; and strict interpolation — because ports are "
        "declared, every `{{ $.key }}` in a prompt is checked against the node's inputs "
        "at load time, turning a silent typo into a fail-fast.",
        "Sub-workflows: a type:\"subflow\" node that runs another workflow JSON as a "
        "single node, so a common draft→critic→revise triad is reused, not copied.",
        "Dynamic fan-out (the one real capability gap, own design doc first): map a "
        "node over a runtime-sized list and gather the results — scatter/gather that "
        "today needs a compile-time constant. Single-machine parallelism through the "
        "existing semaphore; NOT a reopening of the distributed non-goal.",
    )),
    Phase("2026", "Beyond the kernel — as a library, not a platform", (
        "Deeper host-integration examples: run a flow graph as one activity inside a "
        "durable engine (e.g. Temporal) for cross-machine scale.",
        "Richer diagram/observability surfaces built on the P3 event stream.",
        "Distributed execution stays a deliberate non-goal — see Design for why the "
        "single-machine, interpret==compile kernel is the point.",
    )),
)

DOCS = PackageDocs(
    name="flow",
    features_intro="What flow can do today. Each capability is exercised by the shipped "
                   "examples and specified precisely in the Reference.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    roadmap_intro="The runtime-resilience roadmap (P1–P4) and the expressiveness push (P5) are complete — "
                  "structured type-native wire, JSON Schema ports, JSONPath interpolation/mapping with "
                  "end-to-end type checking, multi-output agents, cycle detection, and generated-code "
                  "overrides, each landing with a cross-engine parity test so interpret == compile holds. "
                  "What remains (P6) is numeric conditions, strict interpolation, sub-workflows, and "
                  "dynamic fan-out. flow stays a single-machine, compilable kernel by design; distributed "
                  "execution remains a deliberate non-goal. See Design for the non-goals.",
    roadmap=_ROADMAP,
)
