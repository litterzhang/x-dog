# Task: P3 — structured event stream

Implement roadmap item **P3: Structured event stream**. Today the executor only
logs; there is no machine-readable stream of what happened during a run. Add
typed lifecycle events — `NodeStarted`, `NodeFinished`, `NodeFailed` — carrying
timing and (for agent nodes) token usage, delivered via an optional callback in
the interpreter and via structured logging in the generated module. This is the
foundation for observability and live TUI/web progress.

Everything is under `packages/flow/`. The repo is `mypy --strict` and `ruff`
line-length 120. Do NOT change the returned `runtime` shape or any existing
behaviour when no callback is passed — events are a pure side-channel.

## 1. New module — `packages/flow/src/flow/events.py`

Define frozen dataclasses and a callback type:

```python
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class NodeStarted:
    node_id: str
    step: int

@dataclass(frozen=True)
class NodeFinished:
    node_id: str
    step: int
    duration_s: float        # wall-clock seconds for this node
    tokens: int = 0          # total tokens (agent nodes); 0 for script nodes

@dataclass(frozen=True)
class NodeFailed:
    node_id: str
    step: int
    duration_s: float
    error: str               # f"{type(exc).__name__}: {exc}"

FlowEvent = NodeStarted | NodeFinished | NodeFailed
EventCallback = Callable[[FlowEvent], None]   # import Callable from collections.abc
```

Fully type-annotated. No third-party deps.

## 2. Interpreter — `packages/flow/src/flow/executor.py`

Add a keyword param to `execute(...)`:

```python
    on_event: EventCallback | None = None,
```

In `_run_node`:

- At the top (once the node is going to run — after the `if node_id in completed:
  return` early-out), record `_t0 = time.monotonic()` and emit
  `NodeStarted(node_id, step)`. Use the node's reserved step where available; for
  the start event you can use the current `len(stack)` / the step you reserve.
  Emitting via a tiny local helper `_emit(ev)` that calls `on_event(ev)` only when
  `on_event is not None` keeps the call sites clean and a no-op when disabled.
- On success (both script and agent branches, right after `completed.add(node_id)`
  / `_save_checkpoint()`), emit `NodeFinished(node_id, step, duration_s=monotonic-
  _t0, tokens=<n>)`. For an **agent** node, capture the token count from the
  drained message: `AssistantMessage.usage.total_tokens` (add up across
  TurnEndEvents if more than one). For a **script** node, `tokens=0`.
- On failure (where the executor currently re-raises `last_exc` /
  `agent_last_exc`), emit `NodeFailed(node_id, step, duration_s, error=...)`
  BEFORE re-raising, so a subscriber sees the failure.
- `import time` at the top. The callback is synchronous and best-effort; do not
  let a raising callback break the run — if you want, wrap `_emit` in a
  `try/except Exception: pass` (a subscriber bug must not crash the workflow).

Token capture detail: the agent branch already does
`for part in msg.content: ... accumulated.append(part.text)`. In that same drain,
also accumulate `msg.usage.total_tokens` into a local (guard for `usage` being
present). Use the final total for the NodeFinished event.

## 3. Codegen — `packages/flow/src/flow/codegen.py` + `templates/runtime.py.tmpl`

The generated module has no callback injection point, so it emits the same
lifecycle events via **stdlib logging** to a dedicated logger.

- In the template, add `import logging` and a module logger
  `_EVENT_LOG = logging.getLogger("flow.generated.events")`, plus `import time`.
- Wrap each generated `node_<id>` body so that, when it actually runs (not skipped
  by the `_COMPLETED` guard), it logs a start line, times the work, and logs a
  finish line with the duration; on an exception it logs a failed line and
  re-raises. Keep it simple and uniform, e.g. at DEBUG/INFO:
  `_EVENT_LOG.info("NodeStarted node=%s step=%d", "<id>", len(_STACK))` … and a
  matching `NodeFinished`/`NodeFailed` with `duration_s`. Token accounting in the
  generated module is optional — `tokens` may be omitted there (the generated
  `_run_agent` returns only text); if easy, thread it through, otherwise log
  duration only and note that in the message.
- This must not change the computed `runtime`, so the interpret==compile tests
  stay green (events are logging side-effects). Keep the generated module
  ruff-clean (≤120 or `# noqa: E501`) and mypy-clean. `time.monotonic()` is fine
  in the generated module (it runs as a normal Python program).

## 4. Tests — `packages/flow/tests/test_events.py` (+ maybe extend test_executor)

- The event dataclasses construct and are frozen.
- **Interpreter emits ordered events**: run a small 2-node workflow with an
  `on_event` collector; assert the sequence is
  `NodeStarted(a) … NodeFinished(a) … NodeStarted(b) … NodeFinished(b)`, that
  each NodeFinished has `duration_s >= 0`, and that an agent node's NodeFinished
  carries a `tokens` value from a stubbed `usage` (you can make the fake stream
  return an AssistantMessage with a Usage having a known total_tokens).
- **Failure emits NodeFailed**: a node that raises produces a `NodeFailed` with a
  non-empty `error`, before the run raises.
- **No callback = no change**: a run without `on_event` behaves exactly as before
  (existing tests already cover this; just don't break them).
- Codegen: a generated module logs `NodeStarted`/`NodeFinished` — you can capture
  logs from `flow.generated.events` while exec-ing a generated module, or just
  assert the generated source contains the logging calls.

## 5. Gate — must be green

From the repo root:
```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```
Run them yourself and fix anything you introduce. Do NOT weaken existing tests,
and the `runtime` container and all existing public behaviour must be unchanged
when `on_event` is not passed.
