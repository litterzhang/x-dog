---
title: The Edges of a Static Graph — What flow Cannot Yet Express
description: >
  flow's compile-to-Python guarantee is bought with a static graph. This is an
  honest tour of what that costs — numeric conditions, strict interpolation, and
  the one real capability gap: mapping a node over a runtime-sized list.
date: 2026-07-30 10:00:00
tags: [flow, design, roadmap]
---

flow makes an unusual promise: the same workflow JSON either runs through an
interpreter or compiles to a self-contained, `mypy --strict`-clean Python
module, and the two agree node-for-node. We call it `interpret == compile`, and
it is the whole point of the kernel.

That promise is only cheap to keep because the graph is **static**. Every node
and every edge is known at load time, so the compiler can emit one function per
node and a fixed control-flow skeleton. Nothing about the shape of the run is
decided while it runs. This is a genuine feature — it is why the generated code
is readable, type-checks, and can be dropped into a host with no flow dependency.

But a static graph has edges, in both senses. Having just finished the runtime
resilience roadmap — retries, checkpointing, an event stream, failure isolation,
human-in-the-loop, deterministic reuse — we went looking for the honest answer
to a different question: not *"is it robust?"* but *"what can it not even say?"*
Here is what we found, smallest to largest.

## The condition language can't compare numbers

A conditional edge in flow routes on one of five operators: `equals`,
`contains`, and the boolean combinators `not`, `and`, `or`. Look closely and
you'll notice the two leaf operators are both string operators — `equals` is
string equality, `contains` is substring. There is no `>`.

That sounds minor until you try to write the most natural agent loop there is:
*draft, critique, and stop once the critic's score clears a threshold.* The
score is a number. The kernel gives you no way to say `score >= 0.8`, so you end
up matching against a formatted string and praying nobody emits `0.80` instead of
`0.8`. The fix is small and local — four new operators, `gt/gte/lt/lte`, coerced
through the number path that already exists for script nodes, mirrored in the
one place codegen turns a condition into a Python expression. Low risk, high
everyday value.

## Interpolation swallows your typos

flow fills prompts with a one-line template function: `{{key}}` is replaced by
the matching value, and a missing key becomes the empty string. That last clause
is the problem. Write `{{cotnext}}` instead of `{{context}}` and the template
doesn't error — it quietly drops an entire section of the prompt, the agent runs
on the degraded input, and the workflow *succeeds*. It is the worst kind of bug:
invisible, and dressed as success.

Here the static graph pays us back. Because every node declares its input ports,
we don't need to wait until runtime to catch this. Every `{{key}}` in a prompt
can be checked against the node's declared inputs *at load time* — before a
single token is spent — and an unknown key becomes a validation error that names
the typo and the node. Same check for both engines, because it runs before
either of them. Of everything in this post, this is the highest value for the
least code, and it turns a silent correctness hazard into a fail-fast.

## The one real gap: you can't map over a list

The first two are conveniences and a safety net. This one is a capability the
kernel genuinely lacks.

A loop in flow is a back-edge with a compile-time bound: *run this cycle at most
N times.* N is a literal, baked into the JSON, and the compiler turns it into a
plain `for _ in range(N)`. What you cannot write is this:

> The `plan` node just produced seven subtasks. Run the `work` node once per
> subtask, in parallel, then gather the seven results into `merge`.

Seven is only known at runtime. This pattern — dynamic task mapping, or
scatter-gather — is the bread and butter of production engines
(Airflow's `.expand()`, Prefect's `.map()`, Temporal's child-workflow fan-out),
and flow simply can't model it. You can fake it by pre-declaring a fixed number
of parallel branches and leaving some idle, but the count has to be a constant
and the branches are wired by hand.

It is tempting to say this is impossible under `interpret == compile`, and when I
first looked I said exactly that. Reading the scheduler and the code generator
more carefully, I was wrong. Generated Python can absolutely express
`await asyncio.gather(*[work(x) for x in subtasks])` — the fan-out itself is
easy. The hard part is everything *around* it: the trace and the port store are
keyed by node id, so ten instances of one node need ten distinct keys or they
collide; the checkpoint has to know *which* instances finished so a resumed run
re-runs only the rest; and "gather" needs a defined reduce — list, concat,
first-wins — which is a new kind of edge, not a flag. None of that is
intractable. All of it needs to be designed before it's coded, which is why
dynamic fan-out gets its own design pass rather than a quick patch.

Two things it is *not*: it is not a bug (the kernel does exactly what it was
scoped to), and it is not a reopening of our distributed non-goal. Fanning a node
across a runtime-sized list is single-machine parallelism — it runs through the
same semaphore that already caps concurrency today.

## What this says about the shape of the thing

The pattern across all three is the same. flow trades expressiveness for a
static, compilable graph, and most of what it "can't do" is the shadow of that
trade: stringly-typed data between agents, no sub-workflows to reuse a common
triad, while-loops you have to invert into bounded loops. Almost all of it is
either a small symmetric addition (numeric conditions), a load-time check the
static graph hands us for free (strict interpolation), or composition of
features we already have (structured agent data falls out of nested
interpolation plus the `output_schema` we already ship).

Only one item on the list — dynamic fan-out — actually widens what the kernel can
*express*, and it's the only one that earns a design document before a single
line of code. Everything else is the top-left quadrant: small, local, low-risk,
and useful now.

The full analysis, with the priority matrix and a phased plan that keeps
`interpret == compile` intact at every step, lives in `docs/expressiveness.md`.
The roadmap phase tracking this work is **P5 — Expressiveness**.
