# Task: P4.2 — concurrency caps

Implement roadmap item **P4.2: Concurrency caps**. Today the readiness scheduler
launches EVERY ready node at once (`asyncio.gather` over the whole ready set), so
a very wide fan-out can burst well past an LLM provider's rate limits. Add an
optional cap on how many nodes run concurrently, enforced with an
`asyncio.Semaphore`.

Everything is under `packages/flow/`. `mypy --strict`, `ruff` line-length 120.
Default (no cap) behaviour must be UNCHANGED so all existing tests pass.

## 1. models — `packages/flow/src/flow/models.py`

Add to `WorkflowDef` (keyword, default = unlimited):

```python
    max_concurrency: int = 0   # 0 (or negative) = unlimited (current behaviour)
```

## 2. loader — `packages/flow/src/flow/loader.py`

In `parse_workflow`, read a top-level `data.get("max_concurrency", 0)`. Validate
it is an int `>= 0` (raise `WorkflowValidationError` otherwise, e.g.
`"max_concurrency must be an int >= 0"`). Pass it to `WorkflowDef(...)`.

## 3. executor — `packages/flow/src/flow/executor.py`

Add a keyword param to `execute(...)`:

```python
    max_concurrency: int | None = None,
```

Resolution: the effective cap is `max_concurrency` if it is not None, else
`wf.max_concurrency`. A value `<= 0` means unlimited. So the execute() param
OVERRIDES the workflow field when provided.

Enforcement:

- Build an `asyncio.Semaphore(cap)` once, only when `cap > 0` (else `None`).
- Wrap each node launch so it acquires the semaphore for the duration of its run.
  The cleanest way without touching `_run_node`'s internals: define a small
  wrapper `async def _run_capped(node_id): async with _sem: await
  _run_node(node_id)` when `_sem` is not None, and gather over `_run_capped`
  instead of `_run_node` (fall back to `_run_node` directly when there is no cap).
  Keep the existing `return_exceptions=True` (from P4.1) so isolation still works —
  i.e. the wrapper must let exceptions propagate out of the `async with` so gather
  still sees them per-node.
- Do NOT change the returned `runtime` shape or any other behaviour. When the cap
  is unlimited, the emitted gather must behave exactly as today.

Correctness note: a semaphore bounds how many `_run_node` coroutines are inside
their critical section at once; the scheduler still gathers the whole ready set,
but only `cap` of them proceed past `async with _sem` at a time. That is the
intended semantics (bounded concurrency, not bounded readiness).

## 4. codegen — `packages/flow/src/flow/codegen.py` + template

The generated module already emits `asyncio.gather(...)` for parallel waves.
Make it honour the cap:

- Add a module-level semaphore built from the workflow's `max_concurrency` (emit
  the literal value at generate time). In the template, add near the top:
  `_SEM: asyncio.Semaphore | None = None` and, in `main()` (or module init),
  set it from the generated cap when `> 0`.
- Emit a helper in the template, e.g.:
  ```python
  async def _capped(coro: "Awaitable[None]") -> None:
      if _SEM is None:
          await coro
      else:
          async with _SEM:
              await coro
  ```
  and change the generated `await asyncio.gather(node_a(provider), node_b(provider))`
  wave calls to `await asyncio.gather(_capped(node_a(provider)), _capped(node_b(provider)))`.
  When the workflow's cap is 0/unlimited, emit the SAME code as today (no `_capped`
  wrapper, no `_SEM`) so cap-free workflows generate byte-for-byte unchanged.
- The interpret==compile tests run tiny graphs; a cap does not change the computed
  `runtime`, so agreement holds. Keep the generated module ruff- and mypy-clean.

## 5. Tests — `packages/flow/tests/` (new test_concurrency.py or extend test_executor)

- **Cap bounds concurrency**: build a wide fan-out (e.g. `start -> n1..n6 -> end`)
  where each middle node is a script node that, on entry, increments a shared
  counter, records the max observed concurrent value, sleeps briefly
  (`await asyncio.sleep`), then decrements. With `max_concurrency=2`, assert the
  observed peak concurrency never exceeded 2. With no cap, the peak can exceed 2
  (or just assert the run completes). Use script nodes so no LLM is involved.
- **execute() param overrides the workflow field**: set `wf.max_concurrency=5` but
  pass `execute(..., max_concurrency=1)` and assert the peak was 1.
- **loader validation**: `max_concurrency=-1` (or a non-int) is rejected.
- Codegen: a workflow with `max_concurrency>0` generates a module containing the
  `_SEM` / `_capped` machinery (assert on the source), and one without a cap does
  not.

## 6. Gate — must be green

```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```
Run them yourself. Do not weaken existing tests; unlimited-cap behaviour and the
`runtime` shape must be unchanged.
