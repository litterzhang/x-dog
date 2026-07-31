---
title: Design
---

How flow models a multi-agent pipeline and runs it — the ideas behind the JSON.
For the exact field-by-field schema, the type system, and every validation rule,
see the [Reference](/packages/flow/reference).

## Node-private ports, not shared state

A flow workflow is a graph of nodes connected by edges. Data does not travel
through a shared global dict — each node declares typed input and output ports,
and each edge carries an explicit mapping that says which source output port
feeds which destination input port.

Because the wiring is spelled out rather than implied by matching key names, the
graph can be validated before it runs: unknown ports, two producers feeding one
input, or an unfed required input all fail fast at load time.

## Readiness-based parallel executor

The executor runs nodes concurrently by readiness: a node becomes ready when all
of its non-loop predecessors have completed, and every currently-ready node is
launched at once. A fan-in node simply waits until all of its upstreams finish.

Linear pipelines behave exactly like a sequential run; diamonds and fan-outs get
parallelism for free.

## Conditional and bounded-loop edges

Edges can carry a condition (equals / contains / and / or / not over a source
output port) so branches only fire when their guard holds. A back-edge must
declare a bounded loop (`loop.max`), which is how a review→revise cycle stays
finite.

## One runtime container in, workflow outputs out

Two reserved synthetic nodes bracket every run. The workflow's state block is
exposed as the output ports of a source node called `$in`, so the same graph
runs with different inputs without editing the JSON. Nodes wire their output
ports to a sink node called `$output` with ordinary edges, and those collected
key/value pairs are the workflow's result — flushed the moment each feeding node
finishes, so a looped writer's latest value wins.

`execute()` returns a single runtime container: `ctx` (the last node's
step/id/name), `stack` (a per-node delta trace — one `{step, node, in, out}`
frame per execution, so a looped node's refinement history is visible), `state`
(real-node outputs only), `in` (`$in`), and `out` (`$output`). The CLI prints
`out` by default, falling back to the full container.

## Typed ports and JSON Schema

Every port carries a **JSON Schema** — a scalar (`{"type": "integer"}`) or a
nested object/array (`{"type": "object", "properties": {…}}`). The wire format is
**type-native**: a port value is the live Python value (int, float, bool, list,
dict), not a stringified form, so structure flows between nodes intact and a
downstream node reads a real object. A script node sees its inputs coerced to the
declared top-level type; nested structure is validated (by fastjsonschema), not
re-stringified.

An input port is `required` by default. Marking it `required: false` (the old
`optional`) exempts it from the rule that every declared input must be fed by an
edge — that is how a loop-carried value, absent on the first pass and supplied
only by the back-edge, stays an internal port instead of leaking into the
workflow's user-facing inputs.

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

The same JSON can be executed directly by the runtime, or compiled with codegen
into a single self-contained Python module. In both engines a node is a **pure
function** — `node(provider, ctx, inputs) → outputs` — and a generic **driver**
owns the cross-cutting work (entry guards, input assembly, the retry loop,
output storage, the memo fast-path, the token budget, checkpointing, and
isolation). The generated code keeps node outputs in the same nested port
structure the interpreter uses and builds the identical runtime container, so
the two forms agree node-for-node — enforced by a cross-engine parity suite.

Linear and parallel graphs compile to BFS waves (a lone await, or
`asyncio.gather` for a fan-out); bounded loops become a for-range; and a
workflow with forward conditionals compiles to a topologically-ordered,
guard-gated body instead. The generated module also honours `FLOW_INPUTS` and
`FLOW_PROVIDER` env overrides — parity with the interpreter's `--input` and
`--provider`.

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

## Author visually, review as a diagram

An interactive terminal builder (`xdog-flow build`) edits the graph across a
Builder page (with Graph / Nodes / Edges blocks), a Functions page that shows
each script node's source, and a Tools page listing every built-in and custom
tool. It round-trips JSON losslessly — parse then re-serialise is the identity —
so hand-edited and TUI-edited files stay interchangeable.

The same definition renders four ways: a plain-text listing, a layered
box-drawing ASCII diagram with orthogonal edge routing and right-side lanes for
skip/loop edges, a Graphviz-backed SVG (with a dependency-free fallback), and a
Mermaid flowchart.

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
- **Built-in scheduling.** Cron/interval/event triggers are wired around flow by
  the host, not baked into the engine — the same separation a library keeps from
  its scheduler.
- **Compensation / rollback.** Saga-style compensation earns its keep in
  distributed, long-running, cross-service flows — which flow deliberately isn't.
  Failure cleanup is expressible with existing primitives: an `on_error:isolate`
  node collects failures into `runtime.failed`, and a downstream cleanup node
  reads it.
- **External telemetry export.** Metrics are aggregated in-kernel by
  `MetricsCollector`; pushing them to OpenTelemetry / Prometheus is left to the
  caller, who consumes the P3 event stream and forwards it — keeping third-party
  deps out of a kernel whose whole point is to have none.
