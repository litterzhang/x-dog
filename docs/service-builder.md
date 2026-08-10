# A workflow that builds a service from an idea, one run at a time

Status: **working example** — `packages/flow/examples/service_builder/` ·
Related: [`tasks-service.md`](./tasks-service.md)

## The shape of the answer

You cannot write a fixed workflow that turns an idea into a service in one run.
You *can* write a fixed workflow that **advances a project by one verified
increment**, and run it until there is nothing left to advance. That is a
repeatable process, which is the only kind flow is for.

So the input is the idea, the output of each run is one task's worth of progress,
and the thing that accumulates is the workspace — not anyone's context.

```
run 1     survey → plan → (human approves) ─┐
run 2..n  survey → pick → implement ⇄ verify → record
run n+1   survey → nothing open → done
any run   survey → stalled → escalate to a human
```

## Why it converges

Four properties, none of which are the model's opinion:

**The plan is written once and ordered once.** `pick` takes the first open task
by position. Letting an agent choose each run invites it to keep picking the easy
one, and the hard task is never done.

**Verification is a command, not a judgement.** `verify` runs whatever
`verify.txt` says and reads the exit code. An agent asked "is this done?"
eventually says yes regardless of the truth.

**Absence of a check is a failure, not a pass.** No `verify.txt`, or an empty
one, reports `passed: no`. If "nothing checked it" and "it passed" produced the
same answer, the loop would terminate fastest on a project with no tests.

**A failing task is marked blocked, not left open.** Otherwise the next run picks
the same task, fails the same way, forever. Blocked tasks accumulate into a list
a human can act on.

And a termination guarantee on top: `record` fingerprints the source tree at the
start and end of each run. Two consecutive runs that change nothing set
`stalled`, and the next run routes to a human instead of the work. The
fingerprint deliberately excludes `PLAN.md`, `JOURNAL.md` and `state.json` —
those change every run by construction, so counting them would mean the stall
detector never fires and a scheduled workflow spends money every half hour
looking healthy.

## Why each run starts cold, and why that is good

An agent node's context does not survive a run. That looks like a limitation and
is the feature: it forces every durable fact into `PLAN.md`, `JOURNAL.md` and the
source itself. A run that "remembers" why it did something is a run whose
reasoning cannot be reviewed, resumed, or handed to a different model.

Within a run, `implement` inherits from *itself* across the repair loop, so a
second attempt remembers the first instead of re-deriving it. That is the whole
distinction: continuity where it is cheap and inspectable, amnesia where it would
hide state.

## Where the human sits

Two places, both pauses rather than notifications:

- **After the first plan.** The run pauses at `approve` and ends. Approving is a
  run boundary, not a step inside one — which also means the plan can be edited
  by hand before the next run picks it up. `PLAN.md` is a file.
- **On a stall.** `escalate` pauses for `unblock`.

Everything between is unattended.

## Running it

```bash
xdog-flow run service_builder.json --workspace ./my-service
# ... plans, then pauses at `approve`

xdog-flow run service_builder.json --workspace ./my-service    # one task
xdog-flow run service_builder.json --workspace ./my-service    # the next
```

Or install it on a timer and let it work:

```jsonc
"schedule": {"mode": "timer", "every": "30m"}
```

**This workflow cannot be `--confined`.** `verify` shells out to run the
project's test suite, which is precisely the thing the audit hook cannot follow.
That is the honest trade: a workflow that builds software has to run software.
The workspace still bounds every path the hook *can* see, and the agent nodes are
told where they are.

## What this does not do

It is worth being exact, because "builds the service automatically" invites more
belief than the design supports.

- **It builds what the plan says.** The plan is written by a model from one
  paragraph. If the decomposition is wrong, ten perfect runs produce the wrong
  service. The approval pause exists for exactly this and is the highest-value
  thirty seconds a human spends.
- **Its ceiling is one task per run.** A task too large for a single agent turn
  fails, gets blocked, and waits for a human. That is a feature over a silent
  half-finish, but it means the plan's granularity determines whether the thing
  ever finishes.
- **It cannot tell a passing test suite from a good one.** Tests are written by
  the same process that writes the code. `verify` proves internal consistency, not
  fitness for purpose. A human reading `JOURNAL.md` is still the only thing
  standing between "all tasks ticked" and "this works".
- **Blocked is where hard problems go to sit.** The design converts "stuck" into
  "listed and skipped". That keeps the loop alive at the cost of leaving the
  genuinely difficult work for a person — which is the right default, but it is
  not autonomy.

The useful framing: this automates the *loop*, not the *judgement*. It is a
tireless junior who reads the plan, does the next thing, runs the tests, writes
down what happened, and asks for help instead of guessing — and never gets bored
on run forty.
