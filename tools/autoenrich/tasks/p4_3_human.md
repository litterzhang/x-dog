# Task: P4.3 — human-in-the-loop (pause / await-signal nodes)

Implement roadmap item **P4.3: Human-in-the-loop**. Add a new node type that
pauses the run until an external signal (e.g. a human approval) arrives. This
builds on P2 checkpointing: a paused run persists its progress and stops; when the
signal is delivered, re-running with the same run id resumes from the checkpoint
and the human node now passes.

Everything is under `packages/flow/`. `mypy --strict`, `ruff` line-length 120.
Existing behaviour (no human nodes) must be completely unchanged.

## Concept

A `human` node names a `signal` (a string, e.g. `"approval"`). When the executor
reaches it:

- If that signal is present in the run's delivered `signals`, the node PASSES:
  it produces an output port marking approval, and the run continues.
- If the signal is absent, the run PAUSES: the executor saves a checkpoint (when a
  checkpoint store + run_id are configured) and raises `WorkflowPaused(node_id,
  signal)` so the caller knows the run stopped and why.

To resume: deliver the signal (add it to `signals`) and call `execute` again with
the SAME `checkpoint` + `run_id`; the run restores prior progress and the human
node passes this time.

## 1. models — `packages/flow/src/flow/models.py`

- Extend the node type literal to include `"human"`:
  `type: Literal["agent", "script", "human"] = "agent"`.
- Add a field for the signal name (only meaningful for human nodes):
  `signal: str = ""`.

## 2. errors — `packages/flow/src/flow/errors.py`

Add:
```python
class WorkflowPaused(Exception):
    """Raised when a human node pauses the run awaiting an external signal."""
    def __init__(self, node_id: str, signal: str) -> None:
        super().__init__(f"paused at {node_id!r} awaiting signal {signal!r}")
        self.node_id = node_id
        self.signal = signal
```

## 3. loader — `packages/flow/src/flow/loader.py`

- `_parse_node`: read `signal = str(data.get("signal", ""))`; pass to NodeDef.
- Validation: a `human` node MUST declare a non-empty `signal` and MUST NOT set
  `code`/`run`/`prompt`/`tools` (raise `WorkflowValidationError` otherwise). A
  human node may declare a single output port (its approval marker); if it
  declares none, that's allowed too. Add `human` to wherever the type is validated
  (it currently checks script vs agent — add a human branch that enforces the
  above and skips the agent/script-specific checks).

## 4. executor — `packages/flow/src/flow/executor.py`

- Add a keyword param `signals: set[str] | None = None` to `execute(...)` (the set
  of signals already delivered; default empty).
- In `_run_node`, add a branch for `node.type == "human"` BEFORE the script/agent
  branches:
  - If `node.signal` is in the resolved signals set: treat it as an instant
    success — write its output port(s) with an approval value
    (e.g. `{"approved": "true", "signal": node.signal}` collapsed to the single
    declared output port, or just `"approved"` if one port), record the frame,
    `completed.add`, `_save_checkpoint()`, emit the P3 `NodeFinished`, and return.
  - Else: save a checkpoint (`_save_checkpoint()` — so progress persists) and raise
    `WorkflowPaused(node_id, node.signal)`. This propagates out of `execute` (it is
    NOT caught by the P4.1 isolation handler — only real node failures are; a
    `WorkflowPaused` from a human node should abort-with-pause. The simplest: let
    `_run_node` raise it, and in the scheduler's result loop, if any result is a
    `WorkflowPaused`, re-raise it immediately, before the isolate/fail handling).
- Make sure resume works: because human-node output is written and it's added to
  `completed` + checkpointed, a resumed run (P2 restore) will skip it via the
  existing `if node_id in completed: return` guard — but note a paused node was
  NOT completed, so on resume the executor reaches it again and, with the signal
  now present, it passes. That is the intended flow.

## 5. codegen — `packages/flow/src/flow/codegen.py` + template

Support human nodes symmetrically in the generated module, driven by an env var:

- The generated `main()` reads delivered signals from `FLOW_SIGNALS` (a
  comma-separated env var) into a `set`.
- Emit a `node_<id>` function for a human node that: if its signal is in the set,
  writes its output port with the approval value, appends the stack frame,
  `_COMPLETED.add`, `_save_checkpoint()`, logs `NodeFinished`; else it
  `_save_checkpoint()` and `raise SystemExit(f"PAUSED: {node_id} awaiting
  {signal}")` (a standalone module can't return a rich pause object; a non-zero
  exit + a printed PAUSED line is the generated-module equivalent). Keep the
  `_COMPLETED`/`_ISOLATED` guards at the top like other nodes.
- A workflow with NO human nodes generates byte-for-byte unchanged code.
- interpret==compile tests use workflows without human nodes, so agreement is
  unaffected. Keep the module ruff/mypy clean.

## 6. Tests — `packages/flow/tests/test_human.py`

- **Pause then resume**: a workflow `prep(script) -> gate(human, signal="approval")
  -> finish(script)`. First `execute(..., checkpoint=store, run_id="r1")` with NO
  signals → assert it raises `WorkflowPaused` with `.node_id=="gate"` and
  `.signal=="approval"`, and that `prep` was checkpointed (a store file exists /
  `completed` contains prep). Then `execute(..., checkpoint=store, run_id="r1",
  signals={"approval"})` → assert it completes, `finish` ran, and `prep` did NOT
  re-run (reuse the P2 counter trick).
- **Signal present up front**: passing `signals={"approval"}` on the first run
  never pauses.
- **loader validation**: a human node with empty `signal`, or one that also sets
  `code`, is rejected.
- Codegen: a human-node workflow generates a module that raises SystemExit /
  prints PAUSED when the signal env var is absent (assert on source or exec).

## 7. Gate — must be green

```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```
Run them yourself. Do not weaken existing tests; workflows without human nodes and
the existing `runtime` keys must be unchanged.
