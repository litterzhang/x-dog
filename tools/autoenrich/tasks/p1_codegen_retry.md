# Task: P1 (codegen) — emit per-node retry in the generated module

P1 (per-node retry policy) is already implemented in the **interpreter**
(`flow.executor`): a node with `retry: {max, backoff}` is retried on failure. But
**codegen** currently IGNORES `node.retry` — the generated standalone module runs
each node once and does not retry. This task closes that gap so the two run paths
(interpret vs. compile) behave the same for retry, just like they already agree
on outputs and (as of P2) checkpointing.

Everything is under `packages/flow/`. The repo is `mypy --strict` and `ruff`
line-length 120.

## Background — how a node function is generated today

`flow.codegen` emits one `async def node_<id>(provider)` per node. The body:
assembles `ins`, runs the node's core work, writes `_OUT[...]`, appends a
`_STACK` frame, flushes `$output`, then `_COMPLETED.add(...)` + `_save_checkpoint()`.
The **core work** line is:

- script node (`_render_script_node`): `    _val = {await?}_script_<safe>(_ctx, ...)`
- agent node (`_render_node_function`): `    result = await _run_agent(provider, "<model>", _sys, _usr[, _tools])`

`node.retry` is a `RetryPolicy | None` (fields `max: int`, `backoff: float`) —
directly available on the `NodeDef` passed to those renderers.

## What to implement

Wrap ONLY the core-work call of each node in a retry loop when `node.retry` is set
and `node.retry.max > 0`. Mirror the interpreter's semantics
(`flow.executor._run_node`):

- `max_attempts = 1 + node.retry.max`; retry on any exception; before retry
  `attempt` (1-based) do `await asyncio.sleep(node.retry.backoff * attempt)`; after
  exhausting attempts, re-raise the last exception.
- The retried region is JUST the failing call (the `_script_...(...)` call for a
  script node, or the `await _run_agent(...)` call for an agent node). Everything
  after it (writing `_OUT`, the stack frame, `$output`, `_COMPLETED`,
  `_save_checkpoint`) runs once, only after the call finally succeeds.
- When `node.retry` is None or `max == 0`, emit the SAME code as today (no loop) —
  so existing generated output for retry-free workflows is byte-for-byte unchanged.

### Suggested emission

For a script node with retry, instead of:
```python
    _val = _script_flaky(_ctx, x=_in_x)
```
emit something like:
```python
    _last_exc: BaseException | None = None
    for _attempt in range(<max_attempts>):
        try:
            _val = _script_flaky(_ctx, x=_in_x)
            _last_exc = None
            break
        except BaseException as _exc:
            _last_exc = _exc
            if _attempt + 1 < <max_attempts>:
                await asyncio.sleep(<backoff> * (_attempt + 1))
    if _last_exc is not None:
        raise _last_exc
```
(and the analogous wrapper around `result = await _run_agent(...)` for agent
nodes). `asyncio` is already imported by the template. Keep every emitted line
ruff-clean (≤120 or append `# noqa: E501` like the existing code) and the module
mypy-clean.

Implement this in `packages/flow/src/flow/codegen.py` only — you should not need to
touch the template. Factor the wrapper into a small helper (e.g.
`_render_retry_wrapped(call_lines, node)`) used by both renderers.

## Interpret == compile agreement (do not break it)

The existing `test_codegen.py` / `test_integration.py` run generated modules under
a dry-run stub where nodes do NOT fail, so the retry loop is transparent there —
the final `runtime` (state/out) is unchanged. Verify those suites stay green.
Retry only changes behaviour when a call actually raises.

## Tests — `packages/flow/tests/test_codegen.py` (or a new test)

Add a test that a generated module actually retries:
- Build a workflow with a script node that fails the first N calls then succeeds
  (e.g. inline code using a module-global / file counter), with `retry: {max: N}`.
- Generate the module, exec it in-process (see how `_run_generated` /
  `test_generate_parity` exec generated modules), run `main()`, and assert it
  completed successfully (the node ran N+1 times) — proving the generated code
  retried rather than failing on the first error.
- Also assert (or rely on existing parity tests) that a retry-free workflow's
  generated source is unchanged.

## Gate — must be green

From the repo root:
```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```
Run them yourself and fix anything you introduce. Do not weaken existing tests,
and do not change generated output for workflows without a retry policy.
