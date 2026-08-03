---
title: A Workflow as a Node — Sub-Workflows Without Breaking the Compiler
description: >
  flow can now call a whole workflow as a single node. The interesting part isn't
  the feature — it's the design choice that made it cheap: don't expand the child.
  An opaque sub-workflow keeps the static graph static and makes interpret==compile
  stronger, not weaker.
date: 2026-08-04 10:00:00
tags: [flow, design, subflows]
---

An earlier post, *The Edges of a Static Graph*, took an honest inventory of what
flow couldn't yet express. One item was the absence of a reusable unit: a
`WorkflowDef` was flat — nodes and edges, one level — so a common
draft → critique → revise triad had to be copy-pasted into every workflow that
wanted it. This post is about closing that gap, and about the one decision that
turned a scary multi-week change into a two-day one.

## The obvious design is the expensive one

"Call a workflow as a node" sounds like inlining. Take the child's nodes, splice
them into the parent graph, prefix their ids to avoid collisions, and let the
parent scheduler run the whole thing as one big flattened graph. That is how you'd
draw it on a whiteboard, and it is a trap.

The trap is that flow's runtime concerns are all keyed to a single flat graph. The
output store, the completed-set, the checkpoint, the trace frames — every one of
them is a `dict` keyed by node id. The token budget is one accumulator. Failure
isolation is per-node. Concurrency is one semaphore. Inline the child and you
inherit *all* of it twice: now you have to namespace checkpoints across two levels,
thread a step counter through nested scopes, bubble child tokens into the parent's
budget breaker, decide how a child's isolated failure maps onto the parent node,
and reason about a semaphore acquired inside a semaphore. None of it is
impossible. All of it is surface area, and surface area is where
`interpret == compile` goes to die.

## Don't expand the child

The move that makes the whole thing cheap is refusing to inline. A sub-workflow
node is **opaque**: from the parent's point of view it is one node that runs once
and produces some outputs. What happens inside is the child's business.

Concretely, a `type: "subflow"` node just calls the same `execute()` the top-level
run uses, on the child, as a black box:

```python
child_result = await execute(node.child, inputs=child_inputs, ...)
outputs[node_id] = project(child_result.runtime["out"])
```

That single decision evaporates the entire list above. The child's `execute()`
owns its own checkpoint (under a run-id qualified by the parent node), its own
trace, its own token accounting, its own isolation, its own semaphore. The parent
scheduler never learns any of it — it sees one node complete, exactly like a
script or an agent node. The five cross-cutting concerns don't get *solved*; they
get *contained*, one level down, by code that already exists.

## The trade, stated honestly

Nothing is free. An opaque sub-workflow calls `execute()`, which lives in the
`flow` package — so a generated module that uses a sub-workflow now imports `flow`.
That is a real change: until now, generated modules were deliberately
flow-independent — they inline flow's helpers so the compiled artifact stands
alone on just `ai` and `agent`.

We took the trade, scoped tightly. A workflow with no sub-workflow node is
byte-for-byte as independent as before (there's a regression test that asserts the
generated module contains no `import flow`). Only a sub-workflow-using module gains
the dependency, and the `--portable` bundle simply vendors `flow` alongside `ai`
and `agent` when it detects one. In exchange we delete an entire category of
complexity — and, more interestingly, we make the core guarantee *stronger*.

## Why this makes interpret == compile stronger

Here's the part I didn't expect. Normally every feature has to *prove*
`interpret == compile` — you write the interpreter path, you write the codegen
path, and a parity test runs the same workflow both ways to confirm they agree.
The two paths are different code, so the parity test is doing real work.

For sub-workflows there is nothing to prove. Both engines run the child by calling
the *same* `execute()` function. The interpreter calls it directly; the generated
module imports it and calls it. The child's semantics aren't *matched* across two
implementations — they're *shared*, because it is literally one implementation.
The compiled parent embeds the child as a JSON literal and hands it to the same
runtime the interpreter uses. Parity isn't a property we test for here; it's a
property we can't violate.

The ports tell the same story. A sub-workflow node doesn't declare its own inputs
and outputs — it *derives* them from the child's signature. The child's typed
`$in` (declared, or inferred from how each seed is consumed) becomes the node's
input ports; the child's `$output` becomes its output ports. There is no boundary
to keep in sync because there is no second declaration — the parent's interface to
the child *is* the child's signature.

## What it looks like

The canonical example is the one the gap post named. A `compose` node wraps the
draft → critique → revise triad as a reusable child, referenced by path:

```json
{ "id": "compose", "type": "subflow", "subflow": "./essay_compose.json" }
```

The child (`essay_compose.json`) is a complete, independently-runnable workflow —
you can `run` it on its own. Dropped into a parent as a subflow, its ports are
derived, its critic score flows back out to a script node that gates on it, and
the whole thing compiles to a Python module that calls `execute()` on the embedded
child. Both engines produce the same result on a live provider, because both call
the same function.

## The shape of the lesson

The recurring pattern in flow's design is that the static graph is a constraint
you design *with*, not against. Dynamic fan-out — the other capability gap from the
edges post — got the same treatment: rather than teaching the scheduler about
runtime-sized node sets, we kept the fan group as *one* scheduler node and let the
parallelism happen inside it. Same instinct here. The parent graph stays static
and flat; the dynamic, recursive, nested part is sealed inside a single node that,
from the outside, is as boring as any other.

Sub-workflows ship as **P6 — Expressiveness**, alongside numeric conditions,
strict interpolation, and dynamic fan-out. The full design, including the resume
and recursion boundaries we deferred to a v2, lives in `docs/subflow.md`.
