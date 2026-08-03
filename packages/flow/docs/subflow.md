# flow — Sub-Workflows (G5) Design Doc

Status: draft · Audience: flow maintainers · Prerequisite: read
[`expressiveness.md`](./expressiveness.md) §G5 first.

`expressiveness.md` G5 notes that `WorkflowDef` is flat (`nodes + edges`) with no
primitive to call another workflow as a single node, so a common sub-graph
(draft → critic → revise) must be copy-pasted everywhere. This doc designs the
fix. It **rejects the gap doc's "inline the child module" sketch** in favour of a
smaller, lower-risk approach: **the sub-workflow is one opaque node; the child is
NOT expanded** into the parent graph. Both engines run the child through the same
`flow.executor.execute()`, and `--portable` bundles vendor `flow` alongside
`ai`/`agent`.

---

## 1. The capability

Call a whole workflow as a single node:

> A "draft → critic → revise" triad is authored once as `review.json`. Any parent
> workflow references it as one `subflow` node: the parent maps its ports into the
> child's `$in`, the child runs to completion, and the child's `$output` maps back
> into the parent node's output ports.

```
parent:   plan ──topic──► review(subflow: review.json) ──verdict──► publish
child (review.json):   $in ─► draft ─► critic ─(loop)─► $output
```

The parent sees `review` as **one node that completes once**. What happens inside
the child — its own nodes, loops, trace, checkpoint — is the child's business.

---

## 2. The key decision: DON'T expand the child

The gap doc's sketch was "inline the child module's `main()` / nodes into the
parent module." That is the expensive path, because inlining forces the parent to
**absorb the child's entire run-state surface**. Everything in the executor and
codegen is keyed and structured for a *single flat graph*:

- `outputs` / `completed` / checkpoint `completed` — flat node-id maps
- `stack` trace frames — one per node, flat `step` counter
- `tokens_used` — one global accumulator + budget breaker
- `on_error` isolation — node-level fail/isolate
- `max_concurrency` — one semaphore

Inlining N child nodes into the parent means **synthesising all five of these
across two levels** (nested checkpoint namespaces, cross-level step counters,
token bubbling, isolation mapping, shared-vs-nested semaphores). That is the
4–6 day, high-risk project.

**The insight that removes it: if the child is NOT expanded — if a `subflow` node
just calls `execute(child_wf)` as a black box — then all five concerns stay
_inside_ the child's own `execute()` call and never touch the parent's flat
structures.** The child manages its own checkpoint, trace, tokens, isolation, and
concurrency; the parent node only hands in inputs and reads back `ExecResult`.

| Concern | Inline-expand (gap-doc sketch) | **Opaque node (this doc)** |
|---------|-------------------------------|-----------------------------|
| codegen recursion / id namespacing | required (~2 days) | **none** — one function that calls `execute` |
| checkpoint nesting | parent absorbs child ids | **child's own checkpoint** (derived run-id) |
| trace / step counter | cross-level synthesis | **child's own `stack`**, surfaced under the node |
| token budget | manual bubbling | child returns `tokens_used`; parent adds once |
| isolation / on_error | map child failures into parent | child raises → parent node fails normally |
| max_concurrency | shared/nested semaphore | child runs its own; parent node holds one slot |

The cost of the opaque approach is a **single deliberate trade** (§5). Everything
else gets cheaper.

---

## 3. What it costs: the generated module imports `flow`

Today the generated module is **flow-independent by design**. `bundle.py` says it
outright (bundle.py:31–33):

```python
# Packages the generated module imports at run time. flow itself is NOT needed —
# errors/coerce/runtime and the tool registry are inlined into workflow.py.
_VENDORED_PACKAGES = ("ai", "agent")
```

A `subflow` node breaks that: to run the child, the generated module must call
`flow.executor.execute()` — so it **imports `flow`**. This design accepts that:

- **Scope of the trade:** only a workflow that *uses* a `subflow` node gains the
  `flow` dependency. A workflow with no subflow node stays flow-independent, byte
  for byte as today.
- **`--portable` bundles:** add `flow` to `_VENDORED_PACKAGES` so the bundle
  vendors `flow` too. Bundle users are unaffected (it's vendored + on `sys.path`).
- **Single-file `generate` users:** a generated module *with* a subflow node now
  needs `flow` importable at run time (same as it already needs `ai`/`agent`).

This trade is **worth it**: it buys away the entire inline-expansion complexity
(§2) and, crucially, makes `interpret == compile` *stronger* — see §4.4.

---

## 4. v1 design (opaque subflow node)

### 4.1 Model (`models.py`)

`NodeDef.type` gains `"subflow"`; the child is named by the existing `run`-style
reference (reused, no new field):

```python
type: Literal["agent", "script", "human", "subflow"] = "agent"
# For a subflow node: `run` is a "module.path:workflow" ref OR a path to a child
# workflow JSON (resolved like a script node's run-ref, relative to base_dir).
```

**Ports are DERIVED from the child's signature, not declared.** A subflow node
does not author its own `input_ports` / `output_ports` — they are generated from
the child workflow:

- **input_ports** = the child's typed input signature `workflow_input_schema(child)`
  (one port per key, carrying that key's schema).
- **output_ports** = the child's output signature `workflow_output_schema(child)`
  (one port per `$output` key, carrying its schema).

Because the ports are *derived*, there is no "boundary mismatch" to validate — the
parent literally reads the child's signature. The parent still wires the subflow
node with ordinary mapped edges (feeding those derived input ports, reading those
derived output ports), exactly like any other node; those edges are type-checked
against the derived ports by the normal edge-type validation.

**Strict child signature (option A).** A workflow may only be USED as a subflow if
it has a complete typed input signature. `workflow_input_schema(child)` resolves it
by **explicit-else-infer** (no merge):

- if the child declares `in_schema`, that IS the signature (author authoritative);
- otherwise it is **inferred** from each `$in` key's typed consumers
  (`infer_input_schema`) — the consumer port's type, *not* the seed's runtime value
  (a `"347"` seed feeding an `integer` port infers `integer`).

A subflow reference fails validation (option A, strict) if any child `$in` key that
the parent feeds is **uninferable** — no typed consumer, or conflicting consumers —
and the child did not declare it in `in_schema`. In practice the shipped examples
infer a full signature with zero declaration (trip_planner → string/integer/number,
agent_calculator → integer/integer), so a typed subflow is usually free; only a
genuinely ambiguous key forces the author to declare `in_schema`.

No new edge kind — the parent wires the subflow node with ordinary mapped edges,
exactly like any other node.

### 4.2 Interpreter (`executor.py`)

A new branch in `_run_node` (peer to script/agent/human): load the child once,
run it as a nested `execute()`, map ports across the boundary.

```
child_wf = _load_child(node.run, base_dir)         # cached per node
child_inputs = { child_in_key: ins[port] ... }     # parent ins -> child $in
child_result = await execute(
    child_wf,
    stream_fn_factory=stream_fn_factory,           # inherit the parent's provider wiring
    tool_registry=tool_registry,
    base_dir=child_dir,
    inputs=child_inputs,
    checkpoint=checkpoint,                          # child derives its own run-id (§4.3)
    run_id=f"{run_id}::{node_id}" if run_id else None,
    max_tokens=<remaining budget>,
)
outputs[node_id] = { port: child_result.runtime["out"][k] ... }  # child $output -> parent ports
tokens_used += child_result.runtime["tokens_used"]               # bubble tokens once
# one parent trace frame; the child's own stack is nested under it (§4.5)
```

The child's `execute()` owns its own scheduler, checkpoint, trace, tokens,
isolation, and semaphore — the parent scheduler is **untouched** (it sees one node
that completes once, same as fan-out's `_run_fan_node`).

### 4.3 Checkpoint (v1: coarse, child-owned)

The child run derives its run-id from the parent (`{parent_run_id}::{node_id}`) so
its checkpoint file is distinct and stable across resume. **v1 is coarse:** if the
parent resumes, a subflow node that had not completed re-runs the child **from the
child's own checkpoint** (the child resumes itself). If the child had no
checkpoint, it re-runs whole. There is no parent-level partial view into the child
— the subflow node is one `completed` entry, all-or-nothing at the parent level.
(Finer nesting is a later revision, like fan-out's Strategy B.)

### 4.4 Codegen (`codegen.py`) — the cheap part

The subflow node function does not recurse into codegen. It emits a small function
that calls `execute()` on the child:

```python
async def node_review(provider: object, ctx: RuntimeContext, **ins) -> tuple[dict[str, object], int]:
    from flow.executor import execute            # the accepted flow dependency (§3)
    from flow.loader import parse_workflow
    _child = parse_workflow(_CHILD_review)        # child JSON embedded as a module literal
    _res = await execute(_child, inputs={<child_in>: ins[<port>], ...}, ...)
    _out = {<port>: _res.runtime["out"][<child_out_key>], ...}
    return _out, _res.runtime["tokens_used"]
```

- The child workflow JSON is **embedded as a dict literal** (`_CHILD_review = {...}`)
  so a single-file generated module carries its children with it (no sibling-file
  path dependency); `--portable` needs only to vendor `flow`.
- No changes to `_render_main_body_*`, `_safe_ids`, `_drive`, or the scheduler
  skeleton — the subflow node is just another `node_X` the body awaits.

### 4.5 `interpret == compile` — STRONGER here, not weaker

Both engines run the child through the **same `flow.executor.execute()`**:

- interpreter: calls `execute(child_wf)` directly;
- generated module: `from flow.executor import execute` and calls the **same
  function**.

So the child's execution semantics are byte-identical **because it is literally
the same code path** — unlike the inline sketch, which would have to prove the
generated inlined child matches the interpreter line-for-line. The only surface to
align is the **port projection across the boundary** (parent ins → child `$in`
via `execute(inputs=...)`, child `runtime["out"]` → parent output ports), which is
identical in both engines (both call the same `execute()` and read the same
`ExecResult`) and a parity test covers.

### 4.6 Validation (`loader.py`)

- `type:"subflow"` requires `run` (the child ref); it must not set `code`, and it
  must NOT author `input_ports` / `output_ports` — those are derived from the
  child's signature (§4.1).
- **Strict child signature (option A):** load the child and resolve
  `workflow_input_schema(child)` (explicit-else-infer). Every child `$in` key the
  parent feeds must be typed — declared in the child's `in_schema` or inferable
  from a consumer. An uninferable key (no typed consumer / conflicting consumers)
  with no explicit declaration fails at load: the child cannot be used as a subflow
  until its signature is complete. (No separate "boundary mismatch" check exists —
  the parent's ports ARE the child's signature, so they cannot disagree.)
- **Recursion detection:** a subflow whose child (transitively) references the
  parent workflow is rejected at load time (walk the child-ref graph; a cycle →
  `WorkflowValidationError`). Prevents infinite nesting.
- **v1 scope guards (reject):** a subflow node inside a parent `loop_max` cycle or
  as a `fan_out` worker (the loop×subflow and fan×subflow interactions are out of
  scope for v1; the fan-out limiter in `fan-out.md` is the prerequisite for the
  latter).

---

## 5. v1 non-goals (deliberate)

- **No child expansion / inlining.** The whole point (§2). Sub-graph-level
  scheduling across the boundary is not a goal.
- **No fine-grained resume across the boundary.** Coarse, child-owned checkpoint
  only (§4.3).
- **No subflow inside a loop or as a fan-out worker.** Rejected by the loader.
- **No recursion.** Rejected by the loader.
- **The `flow`-independence of generated modules is given up _only_ for workflows
  that use a subflow node** (§3). This is the single accepted trade.

---

## 6. Risks

1. **Generated modules now depend on `flow` (for subflow users).** *Mitigation:*
   scope it — only subflow-using workflows; `--portable` vendors `flow`; document
   the dependency in the bundle README. A test asserts a non-subflow workflow's
   generated module still imports no `flow`.
2. **Boundary port-mapping drift between engines.** The one alignment surface
   (§4.5). *Mitigation:* a cross-engine parity test with a real child workflow
   (parent → subflow → output), asserting identical `state` + `out`.
3. **Recursion / infinite nesting.** *Mitigation:* load-time child-ref cycle
   detection (§4.6) with a dedicated reject test.
4. **Child checkpoint run-id collisions.** Two subflow nodes referencing the same
   child must not share a checkpoint. *Mitigation:* derive run-id from
   `{parent_run_id}::{node_id}` (node-id-qualified), with a resume test.
5. **Embedded child JSON bloats the generated module.** A large child inlined as a
   literal grows `workflow.py`. *Mitigation:* acceptable for v1; a later option
   can emit children as sibling modules for very large graphs.

---

## 7. Phased delivery (TDD, parity-gated)

1. **Model + loader.** `type:"subflow"`; parse the child ref; boundary-signature
   validation; recursion + scope-guard rejects; serialize round-trip. Unit tests
   only.
2. **Interpreter.** The `_run_node` subflow branch: load child, nested `execute()`,
   map ports, bubble tokens, one parent frame. Interpreter-only integration test
   (parent → child → output), plus a resume test (child-owned checkpoint).
   **Gate.**
3. **Codegen.** Emit `node_X` that embeds the child JSON literal and calls
   `execute()`; the generated module stays ruff-clean. Add `flow` to
   `_VENDORED_PACKAGES` (only when the workflow has a subflow node).
4. **Cross-engine parity.** A `subflow` example in `test_integration.py` /
   `test_subflow.py` run through `execute()` and the generated module, asserting
   identical `state` + `out`. **This is the `interpret == compile` gate for G5.**
5. **Docs.** Update `expressiveness.md` G5 status; note the scoped `flow`
   dependency in the bundle README.

---

## 8. Verification (per the repo's flow discipline)

- `export PATH="$HOME/.local/bin:$PATH"` (cwd resets — use absolute paths).
- `uv run ruff check packages/flow/src` · `uv run mypy --strict packages/flow/src`
  · `uv run pytest packages/flow/tests -q`.
- End-to-end: a parent workflow with a subflow node run via `xdog-flow run` **and**
  via `generate` + module, diffing the collected output; plus a `--portable`
  bundle that vendors `flow` and runs standalone.
- A non-subflow workflow's generated module still imports no `flow` (regression).
- `git checkout -- uv.lock` before commit.

---

## 9. Effort estimate

~2–2.5 days, medium-low risk — versus 4–6 days / high risk for the inline sketch.
The saving is entirely from **not expanding the child**: the five cross-cutting
concerns (§2) stay inside the child's `execute()`, codegen adds one function
instead of a recursive namespace rewrite, and parity is *stronger* because both
engines call the same `execute()`. The only price is the scoped `flow` dependency
for subflow-using generated modules (§3), which the `--portable` bundle absorbs by
vendoring `flow`.
