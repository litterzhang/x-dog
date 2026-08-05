# depins_enrich — an unattended workflow that writes real commits

This is the one example that is not a teaching device. It runs on a small server
every four hours, points a coding Agent at a live Flask site, and lets it commit
and push its own work if — and only if — a chain of deterministic checks admits
it. The commits it produced are ordinary commits in that site's history.

It is checked in as a **case study**, not a template. It is the only example
whose script nodes are `run:` references to sibling modules rather than inline
`code`, which is why it is a directory; and it is the only one where an Agent's
output is admitted by a machine, repaired by a second Agent, and re-checked in a
bounded loop before anything is written.

## The scenario

The target is [depins](https://depins.bcp.942295.xyz), a site that catalogues
DePIN projects and publishes articles about them. It is the kind of project that
is never finished and never urgent: there is always another JSON API endpoint,
feed, filter, or landing page worth adding, and never a reason to do it today.

So the cycle asks a narrow question — *add exactly one small, well-formed,
non-duplicate feature, or add nothing* — and the whole workflow exists to make
"or add nothing" the safe default.

## The graph

```
precheck ─(refused)─────────────────────────► skipped ──► $output
    │
    └─(proceed)─► build ─► scope ─► guards ─► gate ─► validate ─► decide
                                               ▲                    │
                                               │                    ├─(approved)──► commit ──► $output
                                               │                    │
                                               │                    ├─(unfixable)─► revert ──► $output
                                               │                    │
                                               └───── fix ◄─────────┘
                                             while note != "", max 3
```

`precheck` also feeds repo facts straight into `scope`, `guards`, `gate`,
`validate` and `decide`, which the sketch above leaves out;
`xdog-flow graph depins_enrich.json` prints the authoritative version. Eleven
nodes: three Agents (`build`, `validate`, `fix`) and eight script nodes.

**`precheck`** is both the gate and the input hydrator. It refuses if the last
commit is under `min_hours` old, or if the working tree is dirty outside the
paths a cycle is allowed to touch. Everything downstream needs — repo path, ruff
baseline, prompts, the ledger of already-built features — flows out of it, so a
refusal leaves no downstream node with an enabled incoming edge and the entire
chain is skipped. There is no `if proceed:` anywhere.

**`build`** is a Claude Sonnet agent with filesystem and bash tools, told to pick
one feature from the prompt's rules, implement it, and leave a manifest behind.

**`scope`, `guards`, `gate`** are the deterministic admission chain and the point
of the whole exercise:

- `scope` reads what actually changed in the working tree.
- `guards` imports the *live* registries out of the repo the Agent just edited and
  diffs them against a ledger of known keys and slugs. An added article shows up
  as a set difference. This is what rejects a no-op cycle (zero additions), a
  runaway cycle (more than one), and a duplicate. It runs inside the target
  project's own virtualenv, never in the flow process.
- `gate` runs ruff (counting *new* violations against the baseline, so pre-existing
  ones don't block anything), pybabel, and a route sweep that hits every dynamic
  and static URL the app exposes.

**`validate`** is a second Agent, a different model, reading the diff cold: is
this factually coherent, safe, and actually finished?

**`decide`** is a script node, and the only node with a fan of outgoing edges. It
folds four verdicts into one of three outcomes, and it exists as a node — rather
than as conditions spread across the write-side edges — so that `commit`,
`revert` and `fix` each have exactly one gated predecessor. An earlier version
wired the conditions directly onto the write edges and made `commit` reachable
while `approved` was false.

**`fix`** is the repair loop. When the rejection came from `gate` or `validate` —
a lint error, a missed check, a validator objection — the change is worth
repairing rather than throwing away, so a third Agent gets the rejection reason
and the loop re-enters `gate`. A `guards` rejection never reaches it: "you added
three things" or "that already exists" is a scope violation, not a defect, and
there is nothing to repair.

The back-edge is a **`while`, not a `loop`** — bounded at 3. If the fixer still
has not converged after three attempts, the run *fails* (`success: false`,
`stoppedBy: loop_not_converged`, exit 1) rather than quietly stopping on a
half-repaired tree. The dirty tree it leaves behind is reclaimed by the next
cycle's `precheck`.

Deliberately absent: `fix` has no model-controlled "should I keep going" output.
A loop-continuation token the Agent controls is a token it can use to end the run
early while the tree is still broken.

## Running it

You cannot usefully run this against your own machine — it wants a specific
repository, virtualenv, and ledger. What you *can* do is run its test suite,
which needs none of them:

```bash
uv run xdog-flow test packages/flow/examples/depins_enrich/ --allow-script-stub
```

Six cases, covering every terminal state: approved → `commit`; a `guards`
rejection → `revert` without ever consulting the fixer; a `gate` failure →
repaired → committed; a `validate` rejection → repaired → committed; a repair
that never converges → the run fails; and a `precheck` refusal → `skipped`.

The Agent turns are stubbed at the provider call, so prompts are still
interpolated for real and stubbed values are validated against each node's own
output contract. Edges, conditions, the `while` bound, and `$output` collection
all run for real — which is the entire point, because those are what the six
cases are actually asserting about.

## Layout

| File | Role |
|---|---|
| `depins_enrich.json` | the workflow |
| `depins_enrich.test.json` | its six-case suite |
| `cycle.py` | `precheck`, `decide`, `commit`, `revert`, `skipped` — cycle logic and the git write side |
| `nodes.py` | `scope`, `run_guards`, `gate` — flow-shaped wrappers over the two modules below |
| `guards.py` | registry-diff content guards (pure, importable, independently testable) |
| `guards_cli.py` | runs `guards.py` inside the target project's virtualenv |
| `bootcheck.py` | the route sweep `gate` shells out to |
| `prompts/` | the builder and validator system prompts, read at run time from `state_dir` |

The split between `cycle.py`/`nodes.py` and `guards.py`/`bootcheck.py` is not
decorative: the lower two are plain Python that knows nothing about flow, so they
can be run and tested on their own, and the upper two are thin adapters to the
script-node signature `f(ctx, **ports) -> value`.

One convention holds throughout: **a node never raises to mean "stop the
cycle"**. It returns a verdict and the conditional edges decide what runs next.
A raise means something is actually broken.
