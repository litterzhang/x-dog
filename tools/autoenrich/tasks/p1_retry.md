# Task: P1 — per-node retry policy for the flow executor

Implement roadmap item **P1: Per-node retry & timeout policy** for the `flow`
package. A node that fails (raises or times out) should be retried up to a
declared number of times, with an optional backoff, before the failure
propagates. This removes the "one LLM hiccup fails the whole run" mode.

Everything is under `packages/flow/`. The repo is `mypy --strict` and
`ruff` line-length 120. Keep the change cohesive and minimal — do NOT touch
unrelated code.

## 1. `packages/flow/src/flow/models.py`

Add a frozen dataclass and a field on `NodeDef`:

```python
@dataclass(frozen=True)
class RetryPolicy:
    """How many times to retry a failed node, and the backoff between attempts."""
    max: int = 0          # number of RETRIES after the first attempt (0 = no retry)
    backoff: float = 0.0  # seconds; the delay before retry k is backoff * k
```

Add to `NodeDef` (after `web_search_model`, keep it a keyword field with a
default so existing code/tests are unaffected):

```python
    retry: RetryPolicy | None = None
```

## 2. `packages/flow/src/flow/loader.py`

In `_parse_node`, parse an optional `"retry"` object into a `RetryPolicy`:

- Shape: `{"retry": {"max": <int>, "backoff": <number>}}`. `backoff` is optional
  (default 0.0). Absent `"retry"` → `retry=None`.
- Validate: `max` must be an int `>= 0`; `backoff` must be a number `>= 0`.
  On a violation raise `WorkflowValidationError` with a clear message
  (e.g. `f"Node {id!r}: retry.max must be >= 0"`). Do this validation where the
  other node-shape validation lives so a bad workflow fails fast at load time.
- Import `RetryPolicy` from `flow.models`.

## 3. `packages/flow/src/flow/executor.py`

In `_run_node`, both the **script** branch and the **agent** branch currently do
`await asyncio.wait_for(<coro>, timeout=timeout)`. Wrap each of those awaits in a
retry loop driven by `node.retry`:

- Total attempts = `1 + (node.retry.max if node.retry else 0)`.
- On a caught exception (including `asyncio.TimeoutError`), if attempts remain:
  `await asyncio.sleep(node.retry.backoff * attempt)` (attempt starting at 1),
  log `logger.debug("Retrying node %r (attempt %d/%d) after %s", node_id, ...)`,
  and try again.
- When attempts are exhausted, re-raise the last exception (preserve today's
  fail-fast semantics and the exception type).
- Do NOT double-record the stack frame or double-run side effects: only the
  successful attempt should proceed to `_record_frame` / `completed.add`. The
  cleanest approach is a small local async helper, e.g.
  `async def _attempt(coro_factory): ...` that takes a zero-arg factory returning
  a fresh awaitable each try (because an awaited coroutine can't be re-awaited).

Keep the existing `timeout=` behaviour: each attempt still gets `wait_for(...,
timeout=timeout)`.

## 4. Do NOT change codegen

`packages/flow/src/flow/codegen.py` and
`packages/flow/src/flow/templates/runtime.py.tmpl` must be left untouched. The
generated module has no per-node timeout, so retry is an interpreter-only
concept; changing codegen would break the interpret==compile agreement tests.

## 5. Tests — `packages/flow/tests/test_executor.py`

Add tests (follow the existing style in that file — fake stream factories,
`WorkflowDef`/`NodeDef`/`Port` constructed in-code, `async def test_...`):

- A **script** node whose function raises on the first N calls and succeeds on
  call N+1: with `retry=RetryPolicy(max=N)` the run succeeds and the node's output
  is stored; assert the function was called N+1 times.
- The same node with `retry=None` (or `max=0`): the run raises on the first
  failure (assert it raises).
- (Nice to have) `backoff` is honoured — you can inject a tiny backoff like
  `0.0` to keep the test fast; the point is the retry count, not real sleeping.

## 6. Gate — must be green

Before you finish, the following must all pass from the repo root:

```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```

Run them yourself (read-only) and fix anything you introduced. Do not weaken or
skip existing tests. Keep `RetryPolicy` immutable and fully type-annotated.
