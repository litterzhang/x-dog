---
title: Design
---

How Flow turns one typed JSON artifact into a workflow that developers can edit
visually and Coding Agents can generate, validate, and repair. For the exact
field-by-field schema and validation rules, see the
[Reference](/packages/flow/reference).

## Node-private ports, not shared state

A flow workflow is a graph of nodes connected by edges. Data does not travel
through a shared global dict — each node declares typed input and output ports,
and each edge carries an explicit mapping that says which source output port
feeds which destination input port.

Because the wiring is spelled out rather than implied by matching key names, the
graph can be validated before it runs: unknown ports, two producers feeding one
input, or an unfed required input all fail fast at load time.

## One frontier execution model

The interpreter and generated module execute the same frontier transition kernel.
A node becomes ready when its ordinary predecessors complete; the complete ready
frontier runs concurrently in declaration order. Condition-enabled edges provide
inputs, while false edges do not leak values into a destination.

All bounded back-edges entering one destination form a conditional AND loop join:
every source must complete in the current generation and every guard must hold
before that destination runs again, exactly once. Each edge keeps its own `max`
and strict-`while` behavior. Dynamic `fan_out` remains one frontier node whose
runtime instances use a separate concurrency cap and preserve input order.

## One runtime container in, workflow outputs out

Two reserved synthetic nodes bracket every run. The workflow's state block is
exposed as the output ports of a source node called `$in`, so the same graph
runs with different inputs without editing the JSON. Nodes wire their output
ports to a sink node called `$output` with ordinary edges, and those collected
key/value pairs are the workflow's result — flushed the moment each feeding node
finishes, so a looped writer's latest value wins.

`execute()` returns an internal runtime container with `ctx`, `stack`, `state`,
`in`, `out`, failures, memo, and token usage. Process boundaries (`xdog-flow run`
and generated Python) print a stable envelope instead:

```json
{
  "success": true,
  "message": "Workflow completed",
  "output": {},
  "context": {
    "workflow": "...",
    "runId": null,
    "startTime": "...",
    "endTime": "...",
    "durationMs": 42,
    "tokensUsed": 0,
    "lastNode": "..."
  }
}
```

## Typed ports and JSON Schema

Every port carries a **JSON Schema** — a scalar (`{"type": "integer"}`) or a
nested object/array (`{"type": "object", "properties": {…}}`). The wire format is
**type-native**: a port value is the live Python value (int, float, bool, list,
dict), not a stringified form, so structure flows between nodes intact and a
downstream node reads a real object. A script node sees its inputs coerced to the
declared top-level type; nested structure is validated (by fastjsonschema), not
re-stringified.

An input port is `required` by default. Marking it `required: false` exempts it
from the rule that every declared input must be fed by an edge — that is how a
loop-carried value, absent on the first pass and supplied only by the back-edge,
stays internal instead of leaking into the workflow's user-facing inputs.

## JSONPath data flow

A prompt reads a port — or a field inside one — with JSONPath:
`{{ $.plan.tasks[0] }}` pulls the first task out of a structured `plan` port. An
edge map can do the same on the source side: `"map": {"$.verdict.within_budget":
"flag"}` wires a nested boolean straight into a downstream input, and the
loader type-checks that sub-field against the source port's schema. Both the
interpreter and the generated module resolve paths through one shared
`jsonpath-ng` evaluator, so interpolation and conditions mean exactly the same
thing on both run paths.

## Two ways to run: interpret or compile

The same JSON can execute directly or compile to a self-contained Python module.
Codegen embeds workflow-specific node functions, static graph metadata, and the
exact same frontier transition kernel used by the interpreter. It does not
translate the graph into a second BFS/for-loop control-flow implementation.

Cross-cutting behavior stays aligned: enabled-edge input assembly, retries,
structured output, memoization, token budgets, failure isolation, fan-out, and
coherent frontier-batch checkpoints. A cross-engine parity suite enforces that
both paths produce the same node state and `$output`.

## Structured output and web search

An agent's structured output is **derived from its output ports** — no separate
schema to maintain. When an agent declares more than one output port (or a single
non-string port), the engine adds a `submit_result` tool and a directive, derives
a JSON Schema from those ports, validates the submitted object with
fastjsonschema, and fans each field into its own typed port. A plain single
`string` port keeps the agent's reply text verbatim.

An agent node can also enable a built-in `web_search` tool, optionally naming a
distinct browsing model (some models don't browse, so a workflow can run the
node on one model and search with another). Tools beyond the built-ins are
declared in a JSON manifest of `module:function` references, loaded at both run
and generate time.

## Human and Agent authoring

The interactive terminal builder (`xdog-flow build`) edits the graph, script
functions, and tool declarations while round-tripping the canonical JSON. A
future local Web UI will edit the same file — not a second database-owned model —
and add graph forms, validation, execution, structured results, scheduling, and
run inspection.

Coding Agents are another editor for the same artifact. The Flow skill gives them
examples and authoring rules; precise validation errors support a create → validate
→ repair → preview → human-review loop. Git remains the collaboration and history
layer.

The same definition renders as plain text, layered ASCII, Graphviz SVG (with a
dependency-free fallback), or Mermaid.

## Deliberately single-machine — a kernel, not a platform

flow runs one graph in one asyncio event loop in one process: `pip install`,
then `asyncio.run(execute(wf))` — no server, no queue, no database. That is a
chosen boundary, not an unfinished one. Distributed execution is a non-goal:
building it would mean rewriting the executor's core (a node is a closure over
shared in-process state, not a serialisable task) and would force a trade
against the interpret==compile guarantee that makes a workflow compilable to a
self-contained module in the first place.

When a run must scale across machines, embed flow as a **library** — run a flow
graph as one unit of work inside a durable engine like Temporal — rather than
asking flow to become that engine. Keeping the kernel small, type-checked, and
compilable is the value a heavyweight platform cannot offer; competing with
Temporal on distribution would forfeit it. The resilience features flow does
ship (retry, checkpoint/resume, isolation, human-in-the-loop, deterministic
reuse) are all the single-machine, in-kernel kind.

## Non-goals (deliberately out of scope)

- **Distributed execution.** The executor is one asyncio event loop in one
  process; there is no cross-machine worker pool, queue, or per-graph scheduling
  — by design. Cross-machine scale belongs to a durable engine flow is embedded
  in, not to flow itself.
- **Multi-tenancy & auth.** flow is a kernel/library, not a hosted service.
  Isolation, authn/authz, and quotas per tenant are the host environment's job
  (the reference HaveFun runner adds only a single-slot guard for safety).
- **In-engine scheduling daemon.** `xdog-flow scheduling` installs timers and hook
  listeners around generated bundles; the frontier engine itself remains a
  one-shot executor.
- **Compensation / rollback.** Saga-style compensation earns its keep in
  distributed, long-running, cross-service flows — which flow deliberately isn't.
  Failure cleanup is expressible with existing primitives: an `on_error:isolate`
  node collects failures into `runtime.failed`, and a downstream cleanup node
  reads it.
- **External telemetry export.** Metrics are aggregated in-kernel by
  `MetricsCollector`; pushing them to OpenTelemetry / Prometheus is left to the
  caller, who consumes the P3 event stream and forwards it — keeping third-party
  deps out of a kernel whose whole point is to have none.
