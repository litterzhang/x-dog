---
title: Letting an Agent Commit — What a Real Deployment Taught flow
description: >
  A workflow that runs unattended every four hours, points a coding Agent at a
  live site, and lets it push its own commits when a chain of deterministic
  checks admits them. Here is the scenario, the four design decisions that
  survived it, and the defects it found that no test suite had.
date: 2026-08-05 14:00:00
tags: [flow, agents, execution, deployment]
---

Most of flow's examples are teaching devices. They are small, they are honest
about what they demonstrate, and nothing happens if they are wrong.

This one is different. It runs on a small server every four hours, points a
coding Agent at a live Flask site, and lets it commit and push its own work. The
commits it produces are ordinary commits in that site's history — no branch, no
review queue, no human in the loop. It has been running in one form or another
since early July and has written about seventy of them.

This post is about rebuilding that automation on flow, and about what the
exercise found. Some of what follows is design that held up. Some of it is bugs
in flow that only a real deployment was ever going to surface.

## The scenario

The target is a site that catalogues DePIN projects and publishes articles about
them. It is the kind of project that is never finished and never urgent: there is
always another JSON API endpoint, feed, filter, or landing page worth adding, and
never a reason to do it today. A backlog of small, well-specified, low-stakes
work that nobody will ever get to.

That is an unusually good fit for an autonomous loop, and an unusually good test
of one — because the interesting problem is not *generation*. Asking a modern
model to add a `/robots.txt` route to a Flask app is not hard. The hard part is
everything downstream of that:

- How do you know it added *one* thing and not three?
- How do you know it didn't add something that already exists?
- How do you know it didn't quietly break a page that isn't in the diff?
- When it gets it wrong, how do you make sure the wrongness never leaves the
  machine?

The cycle therefore asks a deliberately narrow question — *add exactly one small,
well-formed, non-duplicate feature, or add nothing* — and the entire workflow
exists to make "or add nothing" the cheap, default, hard-to-avoid outcome.

## The shape

Eleven nodes. Three of them are Agents; the other eight are ordinary Python.

```
precheck ─(refused)─────────────────────────► skipped ──► $output
    │
    └─(proceed)─► build ─► scope ─► guards ─► gate ─► validate ─► decide
                                               ▲                    │
                                               │                    ├─(approved)──► commit
                                               │                    │
                                               │                    ├─(unfixable)─► revert
                                               │                    │
                                               └───── fix ◄─────────┘
                                             while note != "", max 3
```

`build` is a Sonnet agent with filesystem and bash tools. `validate` is a
different model reading the resulting diff cold. `fix` is a repair agent. That is
the entire Agent surface — three prompts. Everything between `build` and
`commit` is deterministic code whose job is to *not believe* the Agent.

`guards` is the sharp end of that. It imports the live registries out of the
repository the Agent just edited and diffs them against a ledger of already-known
keys and slugs. A new article shows up as a set difference. This is what rejects
the no-op cycle (zero additions), the runaway cycle (more than one), and the
duplicate — three failure modes that a text-level diff heuristic gets wrong in
both directions, as an earlier version of this code demonstrated at length.

`gate` runs ruff — counting only violations *new* against a baseline captured
before the Agent started, so the repository's pre-existing debt doesn't block
anything — then pybabel, then a sweep that hits every dynamic and static route
the app exposes.

## Four decisions that survived

**A node never raises to mean "stop the cycle."** It returns a verdict, and the
conditional edges decide what runs next. A raise means something is actually
broken, not that the cycle was rejected. This sounds like a style preference and
is not: the moment a rejection is an exception, every caller upstream needs to
know the difference between "we chose not to commit" and "git is missing," and
the graph stops describing the process.

**`precheck` is both the gate and the input hydrator.** It refuses if the last
commit is too recent, or if the working tree is dirty outside the paths a cycle
is allowed to touch. Crucially, *everything* downstream needs — repository path,
ruff baseline, prompts, the ledger — flows out of it. So when it refuses, no
downstream node has an enabled incoming edge, and the whole chain is skipped
structurally. There is no `if proceed:` anywhere in the workflow, and no flag
threaded through six nodes to emulate one.

**`decide` is a node, not a set of conditions on the write edges.** It folds four
verdicts into one outcome. The first version didn't have it — the conditions were
spread across the edges into `commit` and `revert` directly — and that version
had a bug I want to state plainly, because it is the exact bug this whole
architecture is supposed to prevent: `commit` was reachable while `approved` was
false. A node whose predecessors carry independent conditions can be entered on a
path nobody drew. Routing both write branches through a single decision node
gives each of them exactly one gated predecessor, and the question "can this run
when it shouldn't?" becomes readable.

**The repair loop has no model-controlled continuation token.** When the
rejection comes from `gate` or `validate` — a lint error, a failed check, a
validator objection — the change is worth repairing rather than discarding, so
`fix` gets the reason and the loop re-enters `gate`. It would have been natural
to let `fix` emit a "should I keep going" output. It was removed on purpose: a
loop-continuation token the model controls is a token it can use to end the run
early, with the working tree half-repaired, and report success.

A `guards` rejection, by contrast, never reaches the fixer at all. "You added
three things" or "that already exists" is a scope violation, not a defect. There
is nothing to repair.

## `while`, not `loop`

The back-edge is bounded at three. Designing what happens *at* the bound turned
out to be a real fork in the road, and it produced a feature.

flow now has two spellings for a bounded back-edge. They are identical except at
the limit. A `loop` that runs out stops — `success: true` — because its bound is
a budget. A `while` that runs out *fails*: `success: false`, exit 1, and a
`stoppedBy` naming the edge that ran out.

The repair loop is emphatically a `while`. A fixer that has not converged after
three attempts has left a broken working tree; stopping quietly there and
reporting success is the worst available outcome, because the next thing that
reads the exit code believes it. (The tree itself is fine — the next cycle's
`precheck` reclaims it. What isn't fine is lying about it.)

Getting this right exposed that a plain `loop` hitting its bound had been
*indistinguishable from a clean finish*: same success flag, often the same empty
output, and nothing anywhere saying a bound was reached. And the failure envelope
was reporting `lastNode: ""` and `tokensUsed: 0` from the interpreter while the
compiled module reported both correctly from its own trace — the same failing
workflow answering a documented contract two different ways. Both are fixed. A
run now says why it ended.

## What the real machine broke

Four defects surfaced within hours of the first real timer firing. Every one of
them was invisible to the test suite, and in hindsight, obviously so.

**A `Type=oneshot` systemd unit with no explicit bound inherits
`DefaultTimeoutStartSec` — 90 seconds on most distributions.** Any workflow that
talks to a model gets killed mid-run. The installer now always writes an explicit
`TimeoutStartSec`, and `schedule.timeout` makes it authorable. A companion
`schedule.jitter` renders `RandomizedDelaySec`, so workflows sharing an hour
boundary don't all fire on the same instant.

**The bundle builder never copied the workflow's sibling modules.** A `run:
"module:func"` script node compiles to a real import; the interpreter satisfies
it by putting the workflow's own directory on `sys.path`; a bundle runs from
somewhere else entirely and failed at import. The bundle now carries that
directory — the whole sibling set, not just the named modules, because those
modules routinely reach for peers flow cannot see, and a bundle missing one
breaks at 4am, unattended, on a timer.

**Codegen assumed a `run:` script was async.** Whether a referenced function is a
coroutine cannot be settled at compile time — it is resolved at import. Codegen
emitted a bare `await` anyway, so a synchronous run-ref worked interpreted and
raised `TypeError: object dict can't be used in 'await' expression` once
compiled. An `interpret == compile` divergence, flow's one load-bearing
invariant, reachable only through a run-ref workflow.

That last one is the pattern worth naming. All three of these were reachable only
by a workflow with sibling `run:` modules installed on a real schedule — and
until this deployment, *every shipped example used inline `code` and none of them
were scheduled*. The example set was the test suite's blind spot, and it was
blind in exactly the shape of the first serious workflow anyone wrote.

That example now ships, as `examples/depins_enrich/`.

## What the test suite broke

The traffic went the other way too, and this one is my favourite.

Adding the repair loop meant adding test cases for it. Four of six failed
immediately — and not because the workflow was wrong. Firing a bounded back-edge
bumps the generation of the destination's whole downstream region and discards
its record of which incoming edges were enabled. Only the loop's own member edges
were re-enabled afterwards. So a node re-entering the loop lost every input
supplied by an upstream node sitting *outside* the region — nodes that had
completed long ago and were never going to run again.

`gate` re-entered the repair loop with an empty input map. `decide` saw neither
scope nor guards, and rejected every successfully repaired change.

This bug was old, and the shipped `refine_loop` example never showed it, because
its only cross-region input comes from `$in` — whose edges are exempt from the
enabled-edge check. A real upstream node was not exempt. The fix precomputes the
forward edges crossing into each invalidation region and replays the ones whose
source verdict was true; those sources are still completed and their verdicts
still stand, so it restores exactly what the invalidation dropped.

In production this would not have crashed. It would have quietly reverted every
change the fixer successfully repaired, forever, and looked like a fixer that
simply wasn't very good.

## Deleting the driver

The previous generation of this automation was a driver script: a few hundred
lines that called out to guards, gates, and an agent, and held the control flow
in Python. The rewrite deleted it. Rate limiting, input hydration, the decision,
and the git write side are all ordinary nodes in the graph now, and the
scheduling is generated from the workflow's own `schedule` block.

What that bought is not fewer lines — the script modules are still there, they
just stopped being a program and started being node bodies. What it bought is
that the process is now *drawable*, and every branch in it is a real edge that
`xdog-flow graph` prints and `xdog-flow test` can pin. Six cases now cover every
terminal state, including the one that fails the run, and they need no
repository, virtualenv, or network to do it — Agent turns are stubbed at the
provider call, and edges, conditions, the `while` bound, and output collection
all run for real.

The last thing the driver was still doing was making it impossible to answer
"can `commit` run when `approved` is false?" by looking. Now you can look.
