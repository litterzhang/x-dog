# Task: P4.1 — per-branch failure isolation

Implement roadmap item **P4.1: Per-branch failure isolation**. Today a fan-out
runs ready nodes with `asyncio.gather`, which is fail-fast: if one branch raises,
gather propagates immediately and the whole run dies, discarding the sibling
branches' results. Add an opt-in so a node can be marked to **isolate** its
failure — when it fails, only it and its downstream sub-tree are abandoned; every
independent sibling branch still runs to completion.

Everything is under `packages/flow/`. `mypy --strict`, `ruff` line-length 120.
Default behaviour must be UNCHANGED (fail-fast) so every existing test still
passes.

## 1. models — `packages/flow/src/flow/models.py`

Add a field to `NodeDef` (keyword, default preserves today's behaviour):

```python
    on_error: Literal["fail", "isolate"] = "fail"
```

`"fail"` = the current fail-fast semantics (a failure re-raises and aborts the
run). `"isolate"` = if this node fails, its failure is captured, the node and its
downstream sub-tree are skipped, and the rest of the graph continues.
`Literal` is already imported in models.py.

## 2. loader — `packages/flow/src/flow/loader.py`

In `_parse_node`, read `data.get("on_error", "fail")`. Validate it is exactly
`"fail"` or `"isolate"`; otherwise raise `WorkflowValidationError`
(e.g. `f"Node {id!r}: on_error must be 'fail' or 'isolate'"`). Pass it to the
`NodeDef(...)`.

## 3. executor — `packages/flow/src/flow/executor.py`

Change the scheduler's fan-out from fail-fast to per-node isolation-aware:

- Replace `await asyncio.gather(*[_run_node(n) for n in ready])` with
  `results = await asyncio.gather(*[_run_node(n) for n in ready],
  return_exceptions=True)` so one branch's exception does not cancel the others.
- After the gather, walk `zip(ready, results)`. For each node whose result is an
  exception:
  - If the node's `on_error == "isolate"`: record it in a run-level
    `failed: dict[str, str]` as `failed[node_id] = f"{type(exc).__name__}: {exc}"`,
    and mark the node PLUS its transitive (forward, non-loop) successors as
    "isolated" so they are never scheduled. Reuse the existing
    `_transitive_successors(node_id)` helper; keep an `isolated: set[str]` and add
    the node + its successors to it. Do NOT add the node to `completed`.
  - If the node's `on_error == "fail"` (default): re-raise that exception now
    (preserving today's fail-fast — the first such failure aborts the run).
- When discovering successors / seeding pending, skip any node in `isolated` (an
  isolated sub-tree must never run). Also make sure an isolated node is not
  considered "ready" — a downstream node whose predecessor was isolated must not
  run (it never becomes ready because its predecessor never entered `completed`;
  additionally guard so isolated nodes are removed from `pending`).
- Add `failed` to the returned runtime container as a NEW top-level key:
  `runtime["failed"] = failed` (a `dict[str, str]`, empty when nothing was
  isolated). Do not change the existing 5 keys (`ctx/stack/state/in/out`).
- Note the interaction with P3 events: an isolated node still emits `NodeFailed`
  via the existing event path — keep that working (the exception is raised inside
  `_run_node` as today; only the SCHEDULER now catches it instead of propagating).
  Make sure `_run_node` still emits `NodeFailed` before the exception leaves it.

Keep it correct under parallelism (the `failed`/`isolated` mutations happen in the
scheduler loop, which is single-threaded between gathers — no lock needed there).

## 4. codegen — `packages/flow/src/flow/codegen.py` + template

The generated module runs nodes in BFS waves / a topological order. Mirror the
isolation:

- Track a module-level `_ISOLATED: set[str] = set()` and `_FAILED: dict[str, str]`
  in the template.
- For a node with `on_error == "isolate"`, wrap its call so that on exception it
  records `_FAILED[<id>] = f"{type(e).__name__}: {e}"`, adds `<id>` (and, simplest,
  lets the downstream guard handle the sub-tree) to `_ISOLATED`, and returns
  WITHOUT re-raising. Guard the top of every node function with
  `if <id> in _ISOLATED: return` AND make a node whose forward predecessor is in
  `_ISOLATED` also skip (add each successor to `_ISOLATED` when isolating, so the
  simple `if <id> in _ISOLATED: return` guard covers the whole sub-tree — compute
  the transitive successors at generate time).
- A node with the default `on_error == "fail"` is emitted exactly as today (no
  wrapper), so retry-free / isolate-free workflows generate byte-for-byte the same
  code.
- Add `"failed": _FAILED` to the generated `_RUNTIME` dict so both run paths agree
  on the new key.

The interpret==compile tests run under a dry-run stub where nodes do not fail, so
`failed` is `{}` on both sides and `_ISOLATED` stays empty — agreement holds.
Verify test_codegen.py / test_integration.py stay green.

## 5. Tests — `packages/flow/tests/` (new test_isolation.py or extend test_executor)

- **Isolate keeps siblings alive**: a diamond `start -> (good, bad) -> end` where
  `bad` has `on_error="isolate"` and raises. Assert: `good` completed, `bad` is in
  `runtime["failed"]`, and (since `end` depends on `bad`) `end` did NOT run; the
  run did NOT raise.
- **Default fail-fast unchanged**: the same `bad` with default `on_error="fail"`
  makes the whole run raise (assert it raises).
- **on_error validation**: loader rejects `on_error="banana"`.
- Codegen: a workflow with an `isolate` node generates a module that, when a
  branch raises, records `_FAILED` and still finishes — or at minimum assert the
  generated source contains the isolation guard.

## 6. Gate — must be green

```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```
Run them yourself. Do not weaken existing tests. Default (`on_error="fail"`)
behaviour and the existing `runtime` keys must be unchanged.
