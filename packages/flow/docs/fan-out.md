# flow — Dynamic Fan-Out (G1) Design Doc

Status: **v1 shipped (Strategy A)** · Audience: flow maintainers · Prerequisite:
read [`expressiveness.md`](./expressiveness.md) §G1 first.

`expressiveness.md` flags G1 (dynamic fan-out) as *"the one gap that deserves its
own design doc before code."* This is that doc. It resolves the two questions the
gap analysis left open — **instance identity** and **reduce semantics** — and
picks the implementation strategy with the smallest blast radius against the
`interpret == compile` invariant. **Strategy A shipped**: `fan_out` / `fan_in`
edge attributes, an interpreter `_run_fan_node`, a codegen `_drive_fan`, and
cross-engine parity for `N ∈ {0, 1, 3}`.

### What shipped (v1, Strategy A)

- `EdgeDef.fan_out: str | None` (names the source array port) and
  `EdgeDef.fan_in: Literal["list", "concat"] | None` (index-ordered reducers).
  `list` preserves one value per worker instance; `concat` flattens each
  instance's array-valued output one level into one flat list.
- **Interpreter** (`executor._run_fan_node`): runs the worker once per array
  element (parallel `gather`), aggregates each output port into an index-ordered
  list stored under the single worker node id. One `completed` entry, one trace
  frame, one checkpoint node — the scheduler is untouched.
- **Codegen** (`_drive_fan` in the runtime template + `_invoke_expr` routing):
  emits the same runtime-sized `gather` + aggregation; the worker node function
  is unchanged (it consumes one element as a normal input).
- **`fan_in`** selects the collector reducer. `list` reads the worker's already
  aggregated, index-ordered list unchanged; `concat` flattens that list one level
  at the collector. Both interpreter and codegen apply the same reducer.
- **Validation**: array-port + element-type checks; rejects worker-in-a-loop,
  nested fan-out, and a `fan_in` whose source isn't a worker.
- **Tests**: `tests/test_fan_out.py` — interpreter behaviour (N=3/1/0, cap=1
  no-deadlock, agent worker, single frame) and `interpret == compile` parity for
  N ∈ {0,1,3}, generated module ruff-clean.

---


## 1. The capability

Map a node over a list whose length is only known at runtime, then gather the
results:

> The `plan` node produced a list of 7 subtasks. Run the `work` node once per
> subtask, in parallel, then gather the 7 results into the `merge` node.

`7` is a runtime value, so today this is inexpressible: `EdgeDef.loop_max` is a
compile-time constant and codegen compiles loops to `for _loop_i in range(<literal>)`.
This is *dynamic task mapping* — Airflow `.expand()`, Prefect `.map()`, Temporal
child-workflow fan-out — the single most common pattern flow cannot model.

**Concrete target workflow** (the map-reduce parity fixture):

```
$in ──topic──► plan ──tasks[]──► work ──res──► merge ──summary──► $output
                     (fan_out)         (fan_in=list)
```

- `plan` (agent, structured): emits `{"tasks": ["a", "b", "c"]}` — an **array**
  output port `tasks`, length runtime-determined.
- `work` (agent): input port `task` (one array element), output port `res`. Runs
  **once per element**, in parallel.
- `merge` (script/agent): input port `results` fed the **ordered list**
  `[work#0.res, work#1.res, work#2.res]`.

---

## 2. Why it's hard (against the real source)

Everything in the kernel is keyed by **node id**, and the graph is **static** —
that staticness is *why* `interpret == compile` is cheap to hold (codegen emits
one fixed function per node and a fixed control-flow skeleton).

Concrete coupling points (file:line as of this writing):

| Structure | Where | Keyed by |
|-----------|-------|----------|
| `outputs: dict[str, dict]` | executor.py:219 | node id |
| `completed: set[str]` | executor.py:272 | node id |
| checkpoint `completed: list[str]` | executor.py:288, 317 | node id |
| `stack` trace frames | executor.py:230 | node id (in frame) |
| `_is_ready` / `_successors` / `_transitive_successors` / `_activate_loops` | executor.py:626–691 | node id |
| codegen: one function per node + fixed skeleton | codegen.py:514, 630, 694 | node id |

N runtime instances of `work` need N distinct identities or their outputs, trace
frames, and checkpoints collide. That is the crux.

---

## 3. Two strategies (the decision)

The blast radius **is** the strategy choice.

### Strategy B — instances as first-class scheduler nodes (the gap-doc sketch)

Materialise instance ids `work#0 … work#6` and let them flow through the
scheduler: they enter `pending`, `completed`, and the checkpoint's `completed`
list; `_transitive_successors`, `_activate_loops`, and the readiness checks all
learn about "fan groups."

- **Pro:** instance-level resume (a half-finished fan-out resumes only the
  unfinished instances); naturally extends to fanning out an entire **sub-graph**.
- **Con:** touches *every* node-id-keyed structure listed in §2 and the
  checkpoint schema. High risk to `interpret == compile`. This is the doc's
  5-phase project.

### Strategy A — the fan group is ONE scheduler node (chosen for v1)

**Key insight:** put the N-way parallelism *inside* `_run_node("work")`. From the
scheduler's viewpoint, `work` is still a single node that completes once. The
dynamic count is confined to that node's execution and the scheduler never sees
it.

| Structure | Strategy A change |
|-----------|-------------------|
| scheduler `pending` / `ready` / `_is_ready` | **none** |
| `_successors` / `_transitive_successors` / `_activate_loops` | **none** |
| checkpoint `completed` | **none** (still `{"work"}`) |
| static-graph property | **preserved** |
| `outputs` store | gains instance sub-keys `work#i` (additive) |

> **Realization note (as shipped).** The implementation went one step *simpler*
> than the `work#i` sub-key sketch below: instead of storing per-instance keys and
> re-collecting them at fan-in, `_run_fan_node` aggregates each output port into an
> **index-ordered list stored directly under the worker's own port**
> (`_OUT["work"]["res"] = [res_0, res_1, …]`). So `outputs` gains **no** instance
> sub-keys at all, and `fan_in` needs **zero** runtime code — the collector edge is
> a plain mapping that reads the already-aggregated list. The §4.2/§4.4 mechanics
> below describe the same observable semantics (index order, `N=0 → []`,
> `N=1 → [x]`); only the storage key differs.

This is the smallest possible blast radius. **v1 ships Strategy A.** Strategy B
is the documented evolution path if instance-level resume or sub-graph fan-out is
later required.

---

## 4. v1 design (Strategy A)

### 4.1 Model (`models.py`)

Two new optional `EdgeDef` attributes — additive, default `None`, so every
existing workflow and its serialization are unchanged:

```python
@dataclass(frozen=True)
class EdgeDef:
    src: str
    dst: str
    mapping: tuple[tuple[str, str], ...] = ()
    when: Condition | None = None
    loop_max: int | None = None
    fan_out: str | None = None
    fan_in: Literal["list", "concat"] | None = None
```

- **`fan_out`** on the `plan → work` edge: names the source node's **array**
  output port whose elements become per-instance inputs. The edge's `mapping`
  carries `(array_port, worker_input_port)` — element `items[i]` feeds instance
  `i`'s input port. Non-fan-out mappings on the same edge (if any) are shared
  verbatim across all instances.
- **`fan_in`** on the `work → merge` edge selects the reducer. `"list"` passes
  the N instance outputs as an **index-ordered** list; `"concat"` requires each
  instance output to be array-like and flattens those arrays one level, preserving
  instance and intra-array order.

### 4.2 Interpreter (`executor.py`)

**Fan-out happens inside the driver.** When `_run_node("work")` sees an incoming
edge with `fan_out` set:

```
fe        = the fan_out in-edge
items     = outputs[fe.src][fe.fan_out]      # the runtime array
N         = len(items)
shared    = _build_inputs("work") minus the fan_out-mapped port
ins_i     = { worker_port: items[i], **shared }   for i in range(N)

# reuse the pure node fns from the 003f8bf refactor — they already return (dict, tokens)
results   = await gather( _node_agent(node, f"work#{i}", ins_i) for i in range(N) )

for i, (val_i, tok_i) in enumerate(results):
    outputs[f"work#{i}"] = _project(node, val_i)   # per-instance storage
    stack.append(frame for work#i)                 # N trace frames
fan_counts["work"] = N                             # remember the width for fan-in
tokens_used += sum(tok_i)
completed.add("work")                              # ONE node completes
```

**Fan-in happens in `_build_inputs` (executor.py:250).** When assembling
`merge`'s inputs, a `fan_in` edge from `work` reads the instance sub-keys:

```python
if edge.fan_in == "list":
    n = fan_counts.get(edge.src, 0)
    for sport, dport in edge.mapping:
        ins[dport] = [outputs[f"{edge.src}#{i}"][sport] for i in range(n)]
```

New run-local state: `fan_counts: dict[str, int]` (node id → instance width),
sitting beside `loop_counters`.

**Concurrency (as shipped).** The N instances run under a **dedicated** limiter
`WorkflowDef.fan_max_concurrency` (0 = unlimited, the default) — a *separate*
`asyncio.Semaphore` built inside `_run_fan_node`, **not** the scheduler semaphore.
Re-acquiring the scheduler semaphore here would self-nest and deadlock at
`cap == 1`, so the fan node holds its one outer scheduler slot and its instances
acquire only the dedicated fan semaphore. Both engines apply the identical cap
(`_run_fan_node` and the template's `_drive_fan(..., fan_cap)`), so
`interpret == compile` holds. With `fan_max_concurrency = 0` all N instances run
at once (the original behaviour); set it to bound a large fan-out (e.g. 50 agent
instances) against a provider's rate limit. This dedicated limiter is also the
prerequisite for a subflow node becoming a fan-out worker (see `subflow.md`):
without it, N child `execute()` calls each with their own semaphore would run
`N × child_cap` LLM calls unbounded.

### 4.3 Codegen (`codegen.py`)

The waves renderer (`_render_main_body_waves`, codegen.py:694) **already emits
`asyncio.gather`** for parallel nodes — dynamic fan-out is the same shape with a
runtime-sized comprehension:

```python
# instead of a single `await node_work(provider)`:
_items = _OUT["plan"]["tasks"]
_res = await asyncio.gather(*[node_work(provider, _x, _i) for _i, _x in enumerate(_items)])
_OUT["work#list"] = [r[0]["res"] for r in _res]   # (dict, tokens) tuple → project port, index-ordered
```

- The worker node function gains `(element, index)` parameters and returns its
  existing `(dict, tokens)` tuple — already gather-compatible.
- The collecting port is built by an index-ordered list comprehension — the same
  order the interpreter uses (§4.4).
- Empty list `N == 0`: `asyncio.gather()` returns `()`, the comprehension yields
  `[]`. Both engines must special-case this identically (see §4.4).

### 4.4 `interpret == compile` — the three alignment points

1. **Reduce order = instance index order.** Interpreter iterates `range(N)`;
   codegen uses `enumerate` + `gather` (which preserves argument order in its
   result). Both produce `[work#0, work#1, …, work#N-1]`. ✓
2. **Empty array `N == 0`.** Worker runs zero times; `merge` receives `[]`. Both
   engines must yield `[]` (not `None`, not a missing port). A parity fixture
   covers the empty case explicitly.
3. **Single element `N == 1`.** No accidental scalar-vs-list divergence: the
   fan-in port is a **one-element list** `[x]` in both engines, never the bare
   `x`. A parity fixture covers this.

### 4.5 Validation (`loader.py`)

- `fan_out` port must be a declared **array** output port of the edge's source
  (`{"type": "array", ...}`); the worker's fed input port type must match the
  array's `items` type.
- `fan_in` consuming port should be typed as an array on the collector.
- The existing **"unfed input / ambiguous producer"** checks treat the fan group
  as a **single** producer of the collected list — the collector's port is
  "fed" by the `work → merge` fan_in edge, not by N separate producers.
- **Rejections** (v1 scope guards, emitted as clear `WorkflowValidationError`):
  - a node that is both a fan-out worker and part of a `loop_max` cycle
    (instance × loop-count blow-up — out of scope, see §5);
  - **nested** fan-out (a fan-out worker whose own input is another fan_out);
  - a `fan_in` edge whose source node is not a fan-out worker.

---

## 5. v1 non-goals (deliberate blast-radius control)

- **Worker is a single node, not a sub-graph.** Fanning out a whole sub-flow is
  Strategy B territory (and rides on G5 sub-workflows).
- **No fan-out node inside a loop.** The instance × loop-counter interaction is
  unspecified and the gap doc never covered it. Loader rejects it.
- **No nested fan-out.**
- **Reducer is `list` only.** `concat` (flatten array-valued instance outputs)
  comes later.
- **Coarse checkpoint.** A run that dies mid-fan-out re-runs **all** N instances
  on resume (the fan node is one `completed` entry — all-or-nothing). Instance-
  level resume is Strategy B. This is acceptable because a fan node is one
  scheduler unit; it is documented, not hidden.

---

## 6. Risks

1. **Semaphore self-nesting deadlock** (§4.2). The fan node must not hold an
   outer slot while its instances acquire slots. Design the driver so `cap == 1`
   runs instances **sequentially** rather than hanging. *Mitigation:* explicit
   test at `max_concurrency=1` with a 3-element fan-out.
2. **Empty / single-element boundaries** (§4.4 points 2–3) must be byte-identical
   across engines. *Mitigation:* two dedicated parity fixtures (`N=0`, `N=1`) in
   `test_integration.py`, plus the normal `N=3` case.
3. **Loader producer/unfed-input rework.** Treating the fan group as one producer
   is a subtle change to the existing checks and is easy to get wrong on edge
   cases (e.g. a collector port also fed by a non-fan edge). *Mitigation:*
   targeted loader tests for each rejection in §4.5 and for the valid map-reduce.
4. **Trace identity.** N frames under `work#i` keys must not break the existing
   trace/`--explain` rendering (which groups by node id). *Mitigation:* render
   `work#i` frames as children of a `work` group; a trace-shape test.

---

## 7. Phased delivery (TDD, parity-gated)

Mirrors the E1 discipline: interpreter → codegen → parity gate, each step green
before the next.

1. **Model + loader.** `fan_out` / `fan_in` on `EdgeDef`; parse + serialize
   round-trip; validation rules and rejections (§4.5). Unit tests only — no
   execution yet.
2. **Interpreter fan-out.** `_run_node` instance loop + `fan_counts` +
   `_build_inputs` fan-in reducer. Interpreter-only integration test on the
   `N=3` map-reduce; plus `N=0`, `N=1`, and `cap=1` cases. **Gate.**
3. **Codegen fan-out.** Emit the runtime-sized `gather` + index-ordered reduce in
   the waves renderer; worker fn gains `(element, index)`. The generated module
   stays `ruff` + `mypy --strict` clean.
4. **Cross-engine parity.** A `map-reduce` fixture in `test_integration.py` runs
   the same workflow through `execute()` and the generated module and asserts
   identical `merge` output for `N ∈ {0, 1, 3}`. **This is the `interpret ==
   compile` gate for G1.**
5. **Trace + docs.** `work#i` frame grouping; update `expressiveness.md` G1 status
   and the site reference.

---

## 8. Verification (per the repo's flow discipline)

- `export PATH="$HOME/.local/bin:$PATH"` (cwd resets — use absolute paths).
- `uv run ruff check packages/flow/src` · `uv run mypy --strict packages/flow/src`
  · `uv run pytest packages/flow/tests -q`.
- End-to-end: the map-reduce example run via `xdog-flow run` **and** via
  `generate` + module, diffing the collected `merge` port for `N=0/1/3`.
- `git checkout -- uv.lock` before commit.

---

## 9. Open question deferred to Strategy B (not v1)

- **Instance-level resume** and **sub-graph fan-out** both require instances to
  be first-class scheduler citizens (instance ids in `completed` / checkpoint).
  When that need is real, revisit §3 Strategy B — but only then, and with its own
  checkpoint-schema migration note. v1's coarse all-or-nothing resume is the
  correct trade for the smallest safe step.
