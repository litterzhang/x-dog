# flow — Expressiveness Gaps & Remediation Plan

Status: draft · Audience: flow maintainers · Scope: what the workflow *model*
can and cannot express today, and how to close the gaps without breaking the
`interpret == compile` guarantee.

This is a design analysis, not a task list. Every gap below is stated against
the actual source (`models.py`, `conditions.py`, `interpolate.py`, `coerce.py`,
`executor.py`, `codegen.py`), with a concrete repro of what you *can't* write
today.

---

## Framing: what flow is, and why that bounds expressiveness

flow is a **single-machine, type-safe, compilable workflow kernel**. Its
defining property is `interpret == compile`: the same workflow JSON runs through
the interpreter (`execute()`) or compiles to a self-contained, `mypy --strict`
-clean Python module, and the two agree node-for-node. That guarantee is only
cheap to hold because the graph is **static** — the set of nodes and edges is
fully known at load time, so codegen can emit a fixed function per node and a
fixed control-flow skeleton.

Most of the gaps below are the *shadow* of that decision. They are not bugs; the
kernel does exactly what it was scoped to do. The question this document answers
is: **which of these are worth relaxing, and what does each cost the
`interpret == compile` invariant?**

---

## The gaps, most-limiting first

### G1 — No dynamic fan-out (can't map a node over a runtime-sized list)

**This is the only capability-level gap. Everything else is convenience or
correctness.** **→ SHIPPED (v1, Strategy A). See [`fan-out.md`](./fan-out.md).**

Today a loop is a back-edge with a compile-time bound:

```python
# models.py
class EdgeDef:
    ...
    loop_max: int | None = None      # a CONSTANT, known at load time
```

codegen compiles it to a native `for` over a constant range:

```python
# codegen.py — _render_main_body_conditional / _waves
for _loop_i in range(lmax):          # lmax is a literal
    _COMPLETED.discard(node_id)
    await node_X(provider)
```

So you can express *"run this cycle at most N times"*. You **cannot** express:

> "The `plan` node produced a list of 7 subtasks. Run the `work` node once per
> subtask, in parallel, then gather the 7 results into the `merge` node."

…because 7 is only known at runtime. This is *dynamic task mapping*
(Airflow's `.expand()`, Prefect's `.map()`, Temporal child-workflow fan-out) —
the single most common pattern flow can't model. You can hack it by pre-declaring
a fixed number of parallel branches and leaving some idle, but the count must be
a compile-time constant, and the branches are wired by hand.

**Root cause:** the executor's readiness scheduler (`_is_ready` /
`_successors`, executor.py ~585–609) fans out over a *static* edge set. There is
no notion of "one edge, N runtime instances."

**Why it's hard (and why my earlier "impossible" claim was wrong):** codegen
*can* emit `await asyncio.gather(*[_body(x) for x in items])` — dynamic fan-out
is expressible in generated Python. The real cost is everywhere else:

- **Trace & state identity.** `runtime.stack` and `_OUT[node_id]` are keyed by
  node id. Ten instances of one node need ten distinct keys
  (`node_id#0 … node_id#9`) or the trace and port storage collide.
- **Checkpoint granularity.** Resume must know *which* of the N instances
  finished. The checkpoint schema (`completed: list[str]`) would need
  instance-level ids.
- **Fan-in / reduce.** A `gather` edge needs a defined aggregation (list-concat?
  reduce fn? first-wins?) — a new edge kind, not just a flag.
- **Validation.** "unfed input / ambiguous producer" checks (loader) assume a
  node has one instance.

**Fix (phased, largest gap → smallest safe step):**

1. **Model.** Add a `fan_out` edge attribute: `EdgeDef.fan_out: str | None` naming
   the source array port whose elements become per-instance inputs, plus a
   `fan_in` reducer on the collecting edge (`concat` | `list` to start).
2. **Interpreter.** In the scheduler, when a node is reached via a `fan_out`
   edge, materialise N logical instances keyed `node_id#i`; store outputs and
   trace frames under those keys; the `fan_in` edge collects `[node_id#i]` in
   order. Concurrency already flows through the existing semaphore.
3. **Checkpoint.** Extend `completed` to record instance ids; a resumed run skips
   finished instances and re-runs only the rest.
4. **Codegen.** Emit `results = await asyncio.gather(*[node_X(provider, _x) for
   _x in _items])` guarded by the same completion/isolation scaffolding, then a
   reduce into the collecting port.
5. **Cross-engine test.** A `map-reduce` example added to
   `test_integration.py` so interpreter and generated module must agree on the
   fanned output for a runtime-sized list.

**`interpret == compile` impact:** HIGH but tractable. The invariant holds as
long as the reduce order is defined (instance index order) and instance ids are
identical across engines. This is the one gap that deserves its own design doc
before code.

> **Design doc:** [`fan-out.md`](./fan-out.md) resolves the instance-identity and
> reduce-semantics questions and picks the smallest-blast-radius strategy
> (fan group as a single scheduler node, dynamic count confined to one node's
> execution) so the static-graph property — and `interpret == compile` — holds.

---

### G2 — The condition language can't compare numbers

`conditions.py` supports exactly five ops, and the two leaf ops are
**string-only**:

```python
# conditions.py
equals   → interpolate(value) == interpolate(text)     # string equality
contains → interpolate(text) in interpolate(value)     # substring
not / and / or                                          # boolean combinators
```

So a loop that should stop *"when the critic score ≥ 0.8"* cannot be written
directly — score is a number, and there is no `>=`. You are forced into brittle
string hacks (`equals: "0.8"`, or `contains` against a formatted string), which
break the moment the value is `0.80` or `0.8000001`.

**Root cause:** conditions were designed for routing on categorical string
labels (a classifier emitting `"bug"` / `"feature"`), not numeric thresholds.

**Fix (low risk, local):**

1. Add ops `gt` / `gte` / `lt` / `lte` to `Condition.op` (a `Literal` union) and
   `conditions.evaluate`, coercing both sides via `coerce.to_python(..., "number")`
   with a clear error on non-numeric operands.
2. Mirror the four ops in `codegen._condition_to_expr` (the generated expression
   is just `float(a) >= float(b)`).
3. Add condition tests + one codegen parity test.

**`interpret == compile` impact:** LOW. Both engines already share the same
condition surface; this adds four symmetric cases.

---

### G3 — Interpolation silently swallows typos (correctness hazard)

`interpolate.py` is one line, and a missing key becomes the empty string:

```python
# interpolate.py
_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")
def interpolate(template, state):
    return _PATTERN.sub(lambda m: state.get(m.group(1), ""), template)   # ← "" on miss
```

Consequences:

- **Typos vanish.** `{{cotnext}}` interpolates to `""`. The prompt silently
  loses a whole section and the run *succeeds* with a degraded prompt — the
  worst kind of failure: invisible.
- **No nested access.** `{{user.name}}` never matches (`.` isn't `\w`), so
  structured values can't be reached from a template at all.
- **No transforms / defaults.** No `{{x | upper}}`, no `{{x or "n/a"}}`.

**Fix (highest value-for-effort in this document):**

1. Add a **strict mode**: an unknown key raises `WorkflowValidationError` naming
   the key and the node, instead of returning `""`. Make it the default for
   *validation* (load-time check that every `{{key}}` in a prompt is a declared
   input port), and keep runtime lenient-or-strict configurable.
2. Because ports are declared, this can be a **load-time** check: for each node,
   every `{{key}}` in `system_prompt` / `prompt` must be a declared input port
   name. This catches typos before the run, with zero runtime cost, and works
   identically for interpreter and codegen (it's a validation pass, not an
   execution path).
3. (Optional, later) nested access `{{a.b}}` by looking up JSON in the port.

**`interpret == compile` impact:** NONE for the load-time validation (it runs
before either engine). LOW for a runtime strict flag (both engines call the same
`interpolate`).

---

### G4 — Data between agent nodes is stringly-typed

The wire format is a flat `dict[str, str]`; typing exists only at *script* node
boundaries via `coerce.to_python` / `to_state`:

```python
# coerce.py docstring
# Workflow state is a flat dict[str, str]. Typed script nodes convert a stored
# string INTO the declared Python type before a script runs, and BACK to a
# string for storage.
```

`models.py` says the quiet part out loud: *"Agent ports are almost always
string."* So when one agent produces a structured object and another consumes
it, the only channel is a JSON string the downstream agent must re-parse in its
prompt. Type safety — flow's headline property — **does not hold across
agent→agent edges**.

**Root cause:** a deliberate simplification. A flat `dict[str, str]` is trivial
to checkpoint (JSON), interpolate, and reason about. Structured wire values would
complicate all three.

**Fix (medium; do *after* G1/G2/G3):**

- Lean on `output_schema` (already shipped): an agent with a schema emits
  validated JSON into its port. Extend interpolation (G3 nested access) so a
  downstream prompt can pull `{{plan.subtasks}}` from that structured port
  without hand-parsing.
- This is mostly *composition of G3 + existing structured output*, not a new
  primitive — which is why it's ranked low despite sounding fundamental.

**`interpret == compile` impact:** LOW-MEDIUM, and mostly inherited from G3.

---

### G5 — No sub-workflow / reusable unit

`WorkflowDef` is flat: `nodes + edges`. There is no primitive to call another
workflow as a single node.  **→ SHIPPED (v1). See [`subflow.md`](./subflow.md).** So a common sub-graph (e.g. a
"draft → critic → revise" triad) must be copy-pasted into every workflow that
needs it, and — combined with G1 — you can't say *"run this sub-flow once per
item."*

**Root cause:** the model is single-level by construction; it keeps loader
validation and codegen (one function per node) simple.

**Fix (medium; independent of the others):**

1. Add `type: "subflow"` to `NodeDef` with a `run`-style reference to another
   workflow JSON.
2. Interpreter: load and `execute()` the child, mapping the parent node's input
   ports to the child's `$in` and the child's `$output` back to the parent
   node's output ports.
3. Codegen: inline the child module's `main()` as a nested async call, or emit an
   import — inlining keeps the "self-contained module" property of `--portable`.

**`interpret == compile` impact:** MEDIUM. Both engines must agree on child
input/output mapping and on how child checkpoints nest under the parent run id.

> **Design doc:** [`subflow.md`](./subflow.md) — chooses an **opaque node** (the
> child is NOT expanded/inlined; both engines run it through the same
> `flow.executor.execute()`), which removes the five cross-cutting nesting
> concerns and makes `interpret == compile` *stronger*. Trade: a subflow-using
> generated module depends on `flow` (bundles vendor it). ~2–2.5 days.

---

### G6 — Loops are bounded-count, not condition-driven; nesting is awkward

(Related to G1 but distinct.) A loop is *"at most N iterations, exit early if the
back-edge `when` fails."* There's no natural *"while <condition>: continue"*.
You always invert it into a bound plus a per-iteration guard, and the guard is
limited by G2's weak conditions. Nested loops work in codegen (`loop_depth`) but,
combined with the flat string state, are hard to author correctly.

**Fix:** largely *falls out of G2* (real conditions make the exit guard
expressive) — no separate primitive needed. Document the "bounded + guard"
idiom clearly; consider a validation warning when a loop has `loop_max` but no
`when` (an unconditional N-times loop is usually a mistake).

**`interpret == compile` impact:** NONE beyond G2.

---

## Priority matrix

| Gap | What it costs users | Fix effort | `interpret==compile` risk | Rank |
|-----|--------------------|-----------|--------------------------|------|
| **G3** interpolation strict/validation | silent wrong prompts | **Low** | None (load-time) | **Do first** |
| **G2** numeric conditions | can't branch on scores | Low | Low | **Do first** |
| **G1** dynamic fan-out | *can't* map over runtime lists | **High** | High (tractable) | Design doc, then do |
| **G5** sub-workflows | no reuse; no per-item sub-flow | Medium | Medium | After G1–G3 |
| **G4** structured agent data | type-unsafe agent→agent | Medium | Low-Med (rides G3) | After G3 |
| **G6** while-loops | awkward loop authoring | Low | None (rides G2) | Falls out of G2 |

**Reading:** the top-left quadrant (G2, G3) is pure upside — small, local, low
risk, immediately useful, and G3 fixes a *correctness* hazard. G1 is the only
change that expands what flow can fundamentally *express*, and it's the only one
that warrants a dedicated design pass because of its checkpoint/trace/reduce
ramifications.

---

## Sequenced plan

**Phase E1 — Correctness & ergonomics (small, ship together)**
- G3: load-time validation that every `{{key}}` is a declared input port;
  optional runtime strict flag. *Fixes a silent-failure hazard.*
- G2: `gt/gte/lt/lte` numeric condition ops, mirrored in codegen.
- G6: validation warning for `loop_max` without a `when` guard.
- Each with condition/interpolation unit tests **and** a codegen parity test so
  `interpret == compile` is enforced by CI.

**Phase E2 — Sub-workflows (independent, medium)**
- G5: `type:"subflow"` node; nested execute in the interpreter, inlined child in
  codegen; nested checkpoint namespacing; a cross-engine example. → designed in
  [`subflow.md`](./subflow.md) as an **opaque node** (child NOT inlined; both
  engines call the same `execute()`; generated module gains a scoped `flow`
  dependency that `--portable` vendors). **v1 shipped**: inline child, derived
  ports, nested `execute()` + codegen `node_X` calling `execute()`, cross-engine
  parity in `tests/test_subflow.py`.

**Phase E3 — Dynamic fan-out (its own design doc first)**
- G1: `fan_out` / `fan_in` edges; instance-keyed trace, state, and checkpoint;
  `gather`-based codegen; a `map-reduce` integration test that both engines must
  pass. **Do not start before the design doc resolves instance-id and reduce
  semantics.** → resolved in [`fan-out.md`](./fan-out.md); **v1 shipped
  (Strategy A)**: `fan_out`/`fan_in` edges, `_run_fan_node` + `_drive_fan`,
  cross-engine parity for `N ∈ {0,1,3}` in `tests/test_fan_out.py`.
- G4 rides on G1 + G3: once instances carry structured ports and interpolation
  can reach nested fields, agent→agent structured data is mostly free.

**Non-goals (unchanged):** distributed execution, multi-tenancy, external
telemetry export, compensation/rollback. Dynamic fan-out (G1) is *single-machine*
parallelism — it does **not** reopen the distributed non-goal.

---

## Guardrail for every phase

Nothing here is allowed to break `interpret == compile`. Concretely: each new
model feature lands with (a) interpreter support, (b) codegen support, and (c) a
test in `test_integration.py` that runs the same workflow both ways and asserts
identical node outputs. A feature that can't be compiled cleanly to
`mypy --strict` Python is out of scope by definition — that constraint is the
product, not an obstacle.
