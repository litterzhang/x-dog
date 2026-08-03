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
    Feature("Conditional edges", "equals / contains / numeric gt / gte / lt / lte / and / or / not guards over a source output port.", "Modeling"),
    Feature("Bounded loops + cycle detection", "Back-edges declare loop.max so cycles terminate; an unbounded cycle fails validation with its path; a loop with no `when` guard warns.", "Modeling"),
    Feature("Script nodes", "Inline code or a run: module:func reference, imported with the workflow dir on path.", "Modeling"),
    Feature("Sub-workflows", "A type:\"subflow\" node runs another workflow as one opaque node — inline or a `./child.json` path ref; ports are derived from the child's signature.", "Modeling"),
    Feature("Edge type checking", "An edge requires the source and destination port types to match — including a sub-field's type via its schema.", "Modeling"),
    Feature("Typed workflow signature", "$in carries an optional in_schema (else it's inferred from consumers); the $output signature is derived from the sink edges — so a workflow has a checkable I/O type.", "Modeling"),
    Feature("Validate before running", "Unknown ports, unfed inputs, ambiguous producers, unbounded cycles, port-type mismatches, prompt-typo interpolation all fail fast.", "Modeling"),
    # --- Data flow: interpolation & mapping ---
    Feature("JSONPath interpolation", "A prompt reads a field with `{{ $.plan.tasks[0] }}`; both engines share one jsonpath-ng evaluator.", "Data flow"),
    Feature("Strict interpolation", "Because ports are declared, every `{{ $.key }}` in a prompt or condition is checked at load time — a typo is a fail-fast, not a silently dropped section.", "Data flow"),
    Feature("Sub-field edge mapping", "An edge maps a nested field: `\"map\": {\"$.verdict.within_budget\": \"flag\"}`, type-checked against the source schema.", "Data flow"),
    Feature("Structured $in seed", "Initial state carries type-native JSON; a structured seed stays a dict/list, not a Python repr.", "Data flow"),
    # --- Execution: how a run behaves ---
    Feature("Parallel executor", "Readiness-based fan-out/fan-in; every ready node runs concurrently via gather. Multi-entry from the $in frontier.", "Execution"),
    Feature("Dynamic fan-out", "A fan_out edge maps a node over a runtime-sized array (once per element, in parallel); a fan_in edge gathers the results into an index-ordered list.", "Execution"),
    Feature("Pure node + driver", "A node is a pure function (provider, ctx, inputs → outputs); a generic driver owns guards, retry, store, memo, budget, checkpoint.", "Execution"),
    Feature("Concurrency caps", "max_concurrency bounds how many nodes run at once; a separate fan_max_concurrency bounds instances within one fan-out (both default to unlimited).", "Execution"),
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
    Feature("CLI agent backend", "An agent node can set backend:\"claude-cli\"/\"codex-cli\" to run one turn by shelling out to a coding-agent CLI instead of the in-process SDK — no provider/API key needed (the CLI owns auth); structured output maps to the CLI's native schema flag.", "Agents & tools"),
    Feature("CLI tool allow-list", "A CLI agent node NARROWS the CLI's own toolset with allowed_tools (built-ins or mcp__server__tool); flow ships no tools. An empty list runs the tightest sandbox.", "Agents & tools"),
    Feature("Per-node MCP servers", "A CLI agent node declares mcp_servers (an opaque pass-through spec); flow generates the CLI's MCP config, with ${ENV} secret interpolation — the JSON carries the reference, never the token.", "Agents & tools"),
    # --- Scheduling: firing a workflow on its own ---
    Feature("Active / timer scheduling", "A schedule:{mode:\"timer\"} block fires the workflow on an interval (every:\"15m\") or a cron expression; xdog-flow install writes a systemd user timer (or a crontab fallback).", "Scheduling"),
    Feature("Passive / hook scheduling", "A schedule:{mode:\"hook\"} block fires on an external event (http/file), delivering a signal to a fresh run — reusing the human-node pause/resume primitive.", "Scheduling"),
    Feature("Shared hook listener", "All hook workflows on a host share ONE systemd-supervised listener that routes each event (http by path, file by dir) to the right bundle — no port collisions, no per-workflow daemon.", "Scheduling"),
    Feature("Install lifecycle", "xdog-flow install builds the portable bundle + installs the scheduler; --list and --delete manage installed workflows via a JSON registry; --dry-run previews the units without touching the OS.", "Scheduling"),
    # --- Codegen & authoring ---
    Feature("Code generation", "Compile a workflow JSON to a runnable, ruff-clean Python module that mirrors the interpreter node-for-node.", "Codegen & authoring"),
    Feature("Interpret == compile", "Interpreter and generated module agree node-for-node — enforced by a cross-engine parity suite on every feature.", "Codegen & authoring"),
    Feature("Portable bundle", "generate --portable emits a self-contained dir; ai/agent are vendored only when an SDK agent node needs them, so a pure-CLI or script-only bundle drops them (requirements trim to jsonpath-ng). --offline downloads wheels for a no-network install.", "Codegen & authoring"),
    Feature("Runtime overrides", "The generated module honours FLOW_INPUTS (JSON merged into $in) and FLOW_PROVIDER — parity with the interpreter's --input / --provider.", "Codegen & authoring"),
    Feature("Interactive builder TUI", "xdog-flow build with Builder, Functions, and Tools pages; shows subflow nodes and the workflow's typed signature; round-trips JSON.", "Codegen & authoring"),
    Feature("Four diagram renderers", "Text listing, layered ASCII, Graphviz SVG (with fallback), and Mermaid; node boxes are colour-coded by type (agent/script/human/subflow).", "Codegen & authoring"),
)

_FEATURE_CATEGORIES = (
    "Modeling",
    "Data flow",
    "Execution",
    "Resilience",
    "Agents & tools",
    "Scheduling",
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
    Phase("P6", "Expressiveness — closing the gaps", (
        "Done: numeric condition ops (gt/gte/lt/lte) so a loop can branch on a "
        "score, not just a string match; and strict interpolation — because ports "
        "are declared, every `{{ $.key }}` in a prompt or condition is checked "
        "against the declared ports at load time, turning a silent typo into a "
        "fail-fast. A loop with no `when` guard now warns.",
        "Done: a typed workflow signature. $in carries an optional in_schema (else "
        "it's inferred from how each seed is consumed — the consumer's type, not the "
        "seed's value); the $output signature is derived from the sink edges. This "
        "gives a workflow a checkable I/O type at both ends.",
        "Done: sub-workflows — a type:\"subflow\" node runs another workflow as one "
        "OPAQUE node (not inlined), authored inline or as a `./child.json` path ref. "
        "Its ports are derived from the child's signature; both engines call the "
        "same execute() on the child, so interpret == compile holds by construction. "
        "A generated module that uses one imports flow (vendored by --portable). See "
        "docs/subflow.md.",
        "Done: dynamic fan-out — a fan_out edge maps a node over a runtime-sized "
        "array and a fan_in edge gathers the results into an index-ordered list, with "
        "a dedicated fan_max_concurrency cap. The fan group stays ONE scheduler node, "
        "so the static graph stays static. See docs/fan-out.md.",
        "Every item shipped with interpreter + codegen + a cross-engine parity test, "
        "and the builder/graph views render subflow nodes and the typed signature.",
    ), done=True),
    Phase("P7", "CLI agent backend — flow as a skill", (
        "Done: an agent node can set backend:\"claude-cli\"/\"codex-cli\" to run one "
        "turn by shelling out to a coding-agent CLI instead of the in-process SDK. A "
        "CLI agent node needs NO provider (the CLI owns auth); its allowed_tools "
        "narrows the CLI's own toolset; its mcp_servers spec is format-converted into "
        "the CLI's MCP config with ${ENV} secret interpolation. Both engines shell the "
        "same command, so interpret == compile holds.",
        "Done: the SDK block (agent/ai imports + tool registry + _run_agent) is emitted "
        "only when a workflow has an SDK agent node — so a pure-CLI/script generated "
        "module imports no agent/ai and its --portable bundle drops that vendoring.",
        "Done: a flow skill (SKILL.md + example pack) installable into claude/codex, so "
        "a CLI can crystallize a recurring process into a workflow and run it. See "
        "docs/cli-agent.md.",
    ), done=True),
    Phase("P8", "Scheduling — fire a workflow on its own", (
        "Done: a top-level schedule block. mode:\"timer\" fires on an interval "
        "(every:\"15m\") or a cron expression (translated to a systemd OnCalendar); "
        "mode:\"hook\" fires when an external event (http/file) delivers a signal to a "
        "fresh run — reusing the human-node pause/resume primitive.",
        "Done: xdog-flow install builds the portable bundle and installs a systemd user "
        "timer (crontab fallback) or, for hooks, adds a route to ONE shared "
        "systemd-supervised listener; --list / --delete manage installs via a JSON "
        "registry; --dry-run previews the units without touching the OS.",
        "The scheduler wraps the built bundle — every firing is a fresh python <bundle> "
        "run, so the engine and interpret == compile are untouched. Linux/systemd "
        "first. See docs/scheduling.md.",
    ), done=True),
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
    roadmap_intro="The runtime-resilience roadmap (P1–P4), the expressiveness push (P5), and the "
                  "expressiveness-gap closing (P6) are complete — structured type-native wire, JSON "
                  "Schema ports, JSONPath interpolation/mapping with end-to-end type checking, numeric "
                  "conditions, strict interpolation, a typed workflow signature (declared or inferred), "
                  "sub-workflows, and dynamic fan-out — each landing with a cross-engine parity test so "
                  "interpret == compile holds. P7 adds a CLI agent backend (run an agent turn by shelling "
                  "out to claude/codex, no provider) and packages flow as a skill; P8 adds scheduling "
                  "(timer + hook via xdog-flow install). flow stays a single-machine, compilable kernel by "
                  "design; distributed execution remains a deliberate non-goal. See Design for the non-goals.",
    roadmap=_ROADMAP,
)
