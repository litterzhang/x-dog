# Task: cost budget — abort a run when it exceeds a token ceiling

Add an optional per-run **token budget** to the `flow` executor. Today token usage
is reported per node (P3 events) and can be aggregated (MetricsCollector), but
nothing STOPS a run that is overspending. This task adds a ceiling: once a run's
cumulative agent tokens exceed a configured maximum, the run aborts with a clear
error instead of continuing to burn budget.

Single-machine, zero new dependencies. `packages/flow/`, `mypy --strict`, `ruff`
line-length 120. When no budget is set, behaviour is exactly as today.

## Semantics

- The budget is a maximum cumulative **token** count for the whole run (agent
  nodes; script/human nodes contribute 0, same as the P3 `tokens` field).
- It is checked AFTER each node finishes (you cannot predict a node's tokens
  before it runs, so this is post-hoc circuit-breaking): once the running total
  EXCEEDS the budget, raise `WorkflowBudgetExceeded` and stop — no further nodes
  are scheduled. The node that pushed the total over the line has already run
  (that's unavoidable and fine); the point is to not start the NEXT one.
- A budget of 0 or None = unlimited (the default), so every existing test and run
  is unchanged.

## 1. errors — `packages/flow/src/flow/errors.py`

Add, mirroring the existing `WorkflowPaused` style:

```python
class WorkflowBudgetExceeded(WorkflowError):
    """Raised when a run's cumulative token usage exceeds its configured budget."""
    def __init__(self, used: int, budget: int) -> None:
        super().__init__(f"token budget exceeded: used {used} > budget {budget}")
        self.used = used
        self.budget = budget
```

(Subclass `WorkflowError`, consistent with the other workflow errors.)

## 2. executor — `packages/flow/src/flow/executor.py`

- Add a keyword param to `execute(...)`:
  ```python
      max_tokens: int | None = None,
  ```
- Keep a run-level cumulative counter, e.g. `tokens_used = 0`, updated when an
  agent node finishes. The agent branch already computes `total_tokens` for the
  node and emits it in `NodeFinished(..., tokens=total_tokens)` — add
  `tokens_used += total_tokens` right there (guard: only meaningful for the agent
  path; script/human/memo paths contribute 0).
- After a node's tokens are added, if a budget is active
  (`max_tokens is not None and max_tokens > 0`) and `tokens_used > max_tokens`,
  raise `WorkflowBudgetExceeded(tokens_used, max_tokens)`. Raise it so it
  propagates out of `execute()` (it is NOT a node failure — do NOT let the P4.1
  isolation handler swallow it; the simplest place is to check right after the
  agent node records its tokens/finishes, inside `_run_node`, and let it bubble;
  if the scheduler's `gather(return_exceptions=True)` catches it, re-raise it
  immediately like `WorkflowPaused` is handled).
- Expose the total on the runtime container as a new key
  `runtime["tokens_used"] = tokens_used` (an int, 0 when nothing ran), leaving the
  existing keys unchanged. This makes the spend visible even on a successful run.

Be careful with concurrency: `tokens_used` is mutated under the existing
`_state_lock` region where other run state is updated, so parallel agent nodes
don't race on the counter.

## 3. codegen — mirror it (env-driven, like checkpoint/signals)

The generated module should honour the same budget via an env var so `main()`
needs no new argument:

- Read `FLOW_MAX_TOKENS` from `os.environ` at the top of `main()`; when set and
  > 0, enforce it.
- Track a module-level `_TOKENS_USED` and, in the generated agent node bodies (the
  ones that call `_run_agent`), add the node's token count and, if over budget,
  raise `WorkflowBudgetExceeded` (import it in the template). Note the generated
  `_run_agent` currently returns only text — if it does not expose usage, it is
  acceptable for the generated module to count what it can (e.g. 0 when usage
  isn't threaded through) BUT the budget check + `_TOKENS_USED` plumbing and the
  `runtime["tokens_used"]` key must still be present so the shape matches. Prefer
  threading usage through `_run_agent` if it's a small change; otherwise document
  the limitation in a comment. Keep the module ruff/mypy clean.
- Add `"tokens_used": _TOKENS_USED` to the generated `_RUNTIME`. A workflow run
  with no budget env var behaves exactly as before, so interpret==compile tests
  (which set no env var and don't fail nodes) stay green.

## 4. Tests — `packages/flow/tests/test_budget.py`

- **Under budget passes**: a workflow whose agent nodes report known token counts
  (stub a fake stream returning a Usage with a fixed total) runs to completion when
  `max_tokens` is comfortably above the total; assert `runtime["tokens_used"]`
  equals the sum.
- **Over budget aborts**: the same workflow with `max_tokens` set BELOW the total
  raises `WorkflowBudgetExceeded`; assert `.used > .budget` and that a downstream
  node did NOT run (e.g. its output is absent from any partial state, or use a
  counter).
- **No budget = unchanged**: `max_tokens=None` (default) never raises regardless of
  spend.
- **Script-only run**: a run with only script nodes has `tokens_used == 0` and any
  positive budget passes.
- Use the fake-stream helpers the existing tests use (`test_events.py` /
  `test_executor.py`) to control reported token counts.

## 5. Gate — must be green

```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```

Run them yourself. Do not weaken existing tests; the no-budget default and the
existing `runtime` keys (beyond the new `tokens_used`) must be unchanged.
