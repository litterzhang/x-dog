---
title: Deleting the Second Scheduler — Migrating flow to a Frontier Kernel
description: >
  flow ran every workflow twice: once through the interpreter's readiness loop,
  once through a schedule the compiler baked into Python control flow. Two
  mechanisms, one invariant, and a parity suite holding them together. Here is
  what each engine did before, and what replaced both.
date: 2026-08-05 09:30:00
tags: [flow, execution, codegen, refactoring]
---

flow has one load-bearing invariant: `interpret == compile`. A workflow run by
`xdog-flow run` and the same workflow compiled to a standalone Python module must
behave identically — same nodes, same order, same outputs, same loop counts.

For a long time that invariant was maintained the expensive way. The interpreter
had a scheduler. The compiler had a *different* scheduler. A parity test suite ran
every feature through both and compared. It worked, in the sense that it caught
drift. It also meant every new graph feature had to be implemented twice, in two
different idioms, and the second implementation was always the harder one.

This post is about deleting the second scheduler.

## Before: the interpreter resolved the schedule at run time

The interpreter's model was a readiness loop over a pending set. Three functions
carried it:

```python
def _is_ready(node_id: str) -> bool:
    """True if all non-loop predecessors of node_id have completed."""
    for edge in in_edges.get(node_id, []):
        if edge.loop_max is not None:
            continue                      # skip loop back-edges
        if edge.src == IN_NODE_ID:
            continue                      # the $in source is always available
        if edge.src not in completed:
            return False
    return True


def _successors(node_id: str) -> list[str]:
    """Successors reachable from node_id given current outputs."""
    result = []
    for edge in edges_from.get(node_id, []):
        if edge.loop_max is not None:
            continue                      # loop edges handled separately
        if edge.dst == OUT_NODE_ID:
            continue                      # the $output sink is collected, never scheduled
        if edge.when is None or evaluate(edge.when, _source_ports(edge.src)):
            result.append(edge.dst)
    return result


def _activate_loops(just_finished: list[str], pending: set[str]) -> None:
    """Re-activate a back-edge destination when its condition still holds."""
```

Run the ready set concurrently, collect what finished, compute successors, check
loop back-edges, repeat. Everything was decided from live state, which is why the
interpreter handled awkward graphs correctly without special cases: it never
needed to know the shape of the graph in advance.

Note the three separate exclusions for loop edges — `_is_ready` skips them,
`_successors` skips them, `_activate_loops` exists solely for them. Loops were a
side channel bolted onto a forward-only scheduler. That detail matters later.

## Before: the compiler resolved the schedule at compile time

The generated module had no scheduler. It had *control flow* — the schedule,
already resolved, expressed as Python statements. And there were two different
ways of resolving it, chosen by graph shape:

```python
def _render_main_body(wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Body of ``async def main()`` — conditional-aware when forward guards exist."""
    if _has_forward_conditional(wf):
        return _render_main_body_conditional(wf, safe_ids)
    return _render_main_body_waves(wf, safe_ids, use_capped=wf.max_concurrency > 0)
```

**The wave path.** With no forward conditionals, the compiler simulated the
readiness loop at build time — computing each wave of concurrently-runnable nodes
and emitting an `asyncio.gather` per wave:

```python
while pending:
    ready = [n for n in pending if all(p in completed for p in fwd_preds.get(n, []))]
    ...
    calls = [_invoke_expr(n, wf, safe_ids) for n in ready]
    lines.append(f"{ind()}await asyncio.gather({', '.join(calls)})")
```

**The conditional path.** With any forward conditional, waves don't work — you
cannot know at compile time which branch runs. So a second strategy: Kahn
topological sort, emit every node in order, and guard each one with membership in
a `_ran` set built at run time.

```python
# Topological order over forward edges (Kahn), stable in declaration order.
...
lines.append(f"{ind()}_ran: set[str] = set()")
for node_id in order:
    ...
    lines.append(f"{ind()}_ran.add('{_ESC(node_id)}')")
```

Loops, in both paths, became Python `for` loops:

```python
var = f"_loop_i_{loop_depth}"
lines.append(f"{ind()}for {var} in range(_loop_start({key!r}), {lmax}):")
loop_stack.append((key, var))
```

with a `loop_stack` to pair each entry with its exit, per-depth variable names to
avoid shadowing under nesting, and `for`/`else` to detect a strict `while` loop
that exhausted its range without converging.

## The divergence nobody would have predicted

Read the dispatch again. The wave path emits `asyncio.gather`. The conditional
path emits one statement per node, in topological order.

**The conditional path had no parallelism at all.**

So adding a single conditional edge — anywhere in the graph, on a branch unrelated
to the parallel section — flipped the entire generated module from concurrent to
sequential. The interpreter kept running those branches in parallel, because its
readiness loop neither knew nor cared that a conditional existed elsewhere.

The parity suite did not catch this. It compared *outputs*, and the outputs were
identical — sequential execution of a correct schedule produces the same values as
parallel execution of the same schedule. Only the wall-clock differed, and no test
asserted on that.

This is the real argument against two implementations of one idea. It isn't that
they drift into producing wrong answers; a good test suite catches that. It's that
they drift along the axes you did not think to assert on.

## After: one kernel, inlined verbatim

The fix was to make the scheduler a **pure, data-driven state machine** with no
I/O, no async, and no knowledge of what a node does — then have both engines run
that exact code.

`flow/frontier.py` is 422 lines and exposes a small surface:

| Function | Role |
|---|---|
| `build_frontier_spec(wf)` | compile the workflow into literal-safe static metadata |
| `new_frontier_state(spec, completed)` | transient run state, seeded from the entry frontier |
| `take_ready(spec, state)` | lease the ready activations, in node declaration order |
| `complete_batch(spec, state, done)` | commit completions, traverse edges, fire loop groups |
| `replay_completed(...)` / `restore_loop_activations(...)` | rebuild transient state on resume |
| `render_frontier_runtime()` | return the kernel's own source, for inlining |

That last one is the trick:

```python
def render_frontier_runtime() -> str:
    """Return the exact pure transition kernel for standalone generated modules."""
    aliases = (
        "FrontierSpec = dict[str, object]\n"
        "FrontierState = dict[str, object]\n"
        "Activation = tuple[str, int, tuple[str, ...]]\n"
        "Completion = tuple[str, int, dict[str, bool]]\n"
    )
    return aliases + "\n\n".join(inspect.getsource(fn) for fn in _INLINE_FUNCTIONS)
```

`inspect.getsource`. The generated module does not import `flow` — it stays
standalone, which is the whole point of a portable bundle — but the scheduler text
it carries is *character-for-character* the scheduler the interpreter just ran.
Parity is no longer a property the test suite verifies. It is a property of how the
file is built.

The generated `main()` collapsed accordingly:

```python
# before — the schedule, baked into control flow
async def main() -> None:
    provider = ai.provider("copilot")
    await node_research(provider)
    # loop: review -> write (max 2 iterations)
    for _loop_i_0 in range(_loop_start('e3'), 2):
        await asyncio.gather(node_write(provider), node_check(provider))
        _loop_tick('e3', _loop_i_0)
    ...

# after — the schedule, as data
_FRONTIER_SPEC = {
    "nodes": ("research", "write", "review"),
    "entries": ("research",),
    "edges": {...},
    "loop_groups": {"write": (...)},
}

async def main() -> None:
    await _run_generated_frontier()
```

The generated module is now **static metadata + node functions + the shared
kernel**. Its structure no longer varies with graph shape: no strategy dispatch, no
`_ran` sets, no `loop_stack`, no per-depth loop variables, no `for`/`else`.

## What the migration bought

Deleting a scheduler is not usually where features come from, but this one paid
out, because several things that were hard to express in emitted control flow are
ordinary in a state machine.

**Loops stopped being a side channel.** In the old model a back-edge was skipped by
`_is_ready`, skipped by `_successors`, and handled by a third function. In the
frontier model an activation carries a *generation*, and a back-edge simply
advances the destination's generation. That single change made three things fall
out that had previously been out of reach:

- **Multi-edge loop AND joins.** Several bounded back-edges sharing one
  destination now form a conditional join: every member source must complete in the
  current generation and every member condition must hold before the destination
  runs again — exactly once, not once per edge.
- **Heterogeneous bounds.** Each member edge keeps its own `max` and its own
  strict-`while` behavior, so a join can mix a plain bounded edge with a strict one.
- **Conditional input filtering.** A destination waits for every ordinary
  predecessor, but only condition-*enabled* edges contribute mapped input values.
  Previously "which edges supply data" and "which edges gate readiness" were the
  same question; now they are two.

**Checkpointing got a coherent boundary.** This came in the follow-up commit, and
it was only possible after the migration. Node-local checkpoint writes were
replaced by one write per settled frontier batch, behind a shared interceptor that
both engines use. The durability contract became statable in one sentence —
*at-least-once per ready batch* — with the checkpoint schema unchanged. A crash
mid-batch re-runs the batch; a batch that settled is durable.

**codegen shrank.** 1275 lines to 1098 in the migration, and 1063 today — while
gaining features. The 353 deleted lines were almost entirely the second scheduler:
two body renderers, the Kahn sort, the loop bookkeeping, the gate expressions.

## What parity tests still do

They did not go away, and they should not. Inlining the kernel guarantees both
engines make the same *scheduling* decisions; it guarantees nothing about the
layers on either side of it — how the interpreter builds a node's inputs versus how
the generated `_inputs_<node>` function does, how each stores outputs, how each
coerces types.

The honest description is that the parity suite's job got smaller and much better
defined. It used to be asking "do two schedulers agree?", which is an open-ended
question with an infinite surface. Now it asks "do two adapters around one
scheduler agree?" — and one deliberate exception is documented in the code, where
the memo-key digest is expressed twice because the generated module must not import
flow's runtime.

That is the general shape of the lesson. Sharing an implementation is worth more
than testing two implementations against each other, and the reason is not that
tests are weak. It is that the divergence you eventually hit will be on an axis
your assertions never covered — a sequential `gather` that produced perfectly
correct output, just not concurrently.
