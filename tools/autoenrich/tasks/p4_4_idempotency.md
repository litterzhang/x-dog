# Task: P4.4 — deterministic nodes (safe reuse on retry / resume)

Implement roadmap item **P4.4: Idempotency**, modelled as a node **determinism**
flag. A node marked `deterministic: true` is guaranteed to produce the same output
for the same input, so on a RETRY or a RESUME it may safely **reuse a memoised
output instead of re-running** — never repeating side-effects or wasted work. A
node left as the default (`deterministic: false`) is NOT reused: it runs every
time (this is the right default for agent nodes and anything with side-effects or
nondeterminism).

Everything is under `packages/flow/`. `mypy --strict`, `ruff` line-length 120.
Non-deterministic nodes (the default) must behave exactly as today.

## Concept

- A node with `deterministic = true` is memoised: after it runs successfully, its
  output ports are stored in a run-level ledger keyed by the node id AND a hash of
  its assembled input namespace (so a different input re-runs it — determinism is
  "same input ⇒ same output", not "run once ever"). Store on SUCCESS only.
- Before running a deterministic node, compute the same key; if the ledger already
  holds an output for it, REUSE that output (copy into `_OUT[node_id]`, record the
  frame, complete, checkpoint, emit NodeFinished) WITHOUT invoking the
  script/agent.
- The ledger lives in the checkpoint snapshot, so it survives resume: a
  deterministic node that already produced output for a given input in a prior
  (paused/crashed) run is not re-executed on resume. This is the "safe retries"
  the roadmap calls for.
- A non-deterministic node (`deterministic=false`, default) is never consulted
  against or written to the ledger — it always runs.

Note the difference from P2 checkpoint's `completed`: `completed` skips a node
that already finished IN THIS run's progress; the determinism ledger additionally
lets a node be reused across a *retry of the same node* or a resume where the node
was not yet marked completed, keyed by input so a changed input correctly re-runs.

## 1. models — `packages/flow/src/flow/models.py`

Add to `NodeDef` (keyword, default = not deterministic → always runs):
```python
    deterministic: bool = False
```

## 2. loader — `packages/flow/src/flow/loader.py`

`_parse_node`: `deterministic = bool(data.get("deterministic", False))`; pass to
NodeDef.

## 3. executor — `packages/flow/src/flow/executor.py`

- Introduce a run-level ledger `memo: dict[str, dict[str, str]] = {}` mapping a
  memo key -> the node's output ports.
- Memo key helper: `_memo_key(node_id, ins)` = e.g.
  `f"{node_id}:{hashlib.sha256(json.dumps(ins, sort_keys=True).encode()).hexdigest()}"`.
  `hashlib` / `json` are available (json already imported; add `import hashlib`).
- Restore the ledger from the checkpoint snapshot when resuming: add `"memo"` to
  the snapshot in `_save_checkpoint`, and read `snap.get("memo", {})` in the restore
  block alongside outputs/completed/etc.
- In `_run_node`, only for a node with `node.deterministic` true:
  - After assembling `ins` (and after the `if node_id in completed: return` guard),
    compute `k = _memo_key(node_id, ins)`. If `k in memo`: set
    `outputs[node_id] = dict(memo[k])`, record the frame (in = ins, out = reused
    ports), `completed.add`, `_save_checkpoint()`, emit `NodeStarted` +
    `NodeFinished` (duration ~0), and `return` — skipping the script/agent call.
  - After a normal (miss) run of a deterministic node SUCCEEDS and its output ports
    are written, store `memo[k] = dict(outputs.get(node_id, {}))` inside the
    `_state_lock` region before/at `_save_checkpoint`. Only on success (a failed +
    retried deterministic node must not memoise a failure).
- Non-deterministic nodes never touch `memo`.
- Add the ledger to the returned `runtime` as a NEW top-level key
  `runtime["memo"] = memo` (empty dict when unused), leaving the existing keys
  unchanged.

## 4. codegen — `packages/flow/src/flow/codegen.py` + template

Mirror it:
- Add a module global `_MEMO: dict[str, dict[str, str]] = {}` (restored from / saved
  to the checkpoint alongside the existing `_load_checkpoint`/`_save_checkpoint`),
  and `import hashlib` in the template.
- For a `deterministic` node, emit — after `ins` is built and after the
  `_COMPLETED`/`_ISOLATED` guards — a memo-key computation and a hit-guard: if the
  key is in `_MEMO`, copy the stored ports into `_OUT[<id>]`, append the stack
  frame, `_COMPLETED.add`, `_save_checkpoint()`, and `return`. After a normal run
  of a deterministic node, emit `_MEMO[<key_expr>] = dict(_OUT[<id>])`.
- A non-deterministic node generates byte-for-byte unchanged code. interpret==
  compile tests use non-deterministic (default) workflows, so agreement holds.
- Add `"memo": _MEMO` to the generated `_RUNTIME`. Keep ruff/mypy clean.

## 5. Tests — `packages/flow/tests/test_deterministic.py`

- **Deterministic node reused on resume**: a deterministic script node that
  increments a shared counter when it actually runs. Run once with a checkpoint
  store + run_id (it runs, counter=1). Run again with the same store/run_id — the
  node reuses its memoised output and the counter STAYS 1; the output is identical.
- **Different input re-runs**: the same deterministic node with a DIFFERENT input
  produces a new memo entry and runs again (counter increments) — proving the key
  includes the input, not just the node id.
- **Non-deterministic default runs every time**: a node without `deterministic`
  runs on every execute (counter increments each run), i.e. it is never reused.
- Codegen: a deterministic-node workflow generates a module containing the `_MEMO`
  machinery (assert on source); a default workflow does not.

## 6. Gate — must be green

```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```
Run them yourself. Do not weaken existing tests; the default (non-deterministic)
behaviour and the existing `runtime` keys must be unchanged.
