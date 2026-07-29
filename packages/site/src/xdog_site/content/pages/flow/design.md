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

## Two ways to run: interpret or compile

The same JSON can be executed directly by the runtime, or compiled with codegen
into a single self-contained Python module. The generated code keeps node
outputs in the same nested port structure the interpreter uses and builds the
identical runtime container, so the two forms agree node-for-node — and the
emitted module passes the same ruff and mypy `--strict` gate as hand-written
code.

Linear and parallel graphs compile to BFS waves (a lone await, or
`asyncio.gather` for a fan-out); bounded loops become a for-range; and a
workflow with forward conditionals compiles to a topologically-ordered,
guard-gated body instead. The interpreter's port-local prompt interpolation and
source-node condition evaluation are reproduced exactly.

## Typed ports and optional inputs

Every port carries a JSON type (string, integer, number, boolean, array,
object). A script node sees its inputs coerced to Python values by that type and
returns values coerced back to the string wire format; agent ports are almost
always strings. An empty value coerces to the type's zero-value (0, 0.0, false,
[], {}).

An input port can be marked optional, which exempts it from the rule that every
declared input must be fed by an edge. That is how a loop-carried value — absent
on the first pass and supplied only by the back-edge — stays an internal port
instead of leaking into the workflow's user-facing inputs.

## Structured output and web search

An agent node can declare an `output_schema`: the engine adds a `submit_result`
tool and a directive, and the validated JSON the agent submits becomes the
node's output port — no brittle parsing of free-form text.

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
