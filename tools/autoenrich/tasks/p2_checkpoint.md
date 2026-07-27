# Task: P2 — checkpoint & resume for flow

Implement roadmap item **P2: Checkpoint & resume**. A run should be able to
persist its progress after each node, so that if it crashes it can be re-launched
and resume from where it left off — skipping already-completed nodes instead of
redoing them (and re-calling their LLMs).

Everything is under `packages/flow/`. The repo is `mypy --strict` and `ruff`
line-length 120. Keep the change cohesive. Both the **interpreter** (executor)
and **codegen** (generated module) must support checkpointing, and they must
still agree node-for-node (the interpret==compile tests must stay green).

## Concept

A run is identified by a `run_id` (a string). A **CheckpointStore** persists a
JSON-serialisable snapshot of the run's progress under that id and can load it
back. After each node completes, the executor saves a fresh snapshot. When a run
starts with an existing checkpoint for its `run_id`, it restores that snapshot
and does NOT re-run any node already recorded as completed — their stored outputs
are reused.

The snapshot must capture exactly the mutable run state the scheduler needs:

- `outputs` — the nested `{node_id: {port: value}}` store (INCLUDING the `$in`
  key), which already holds every completed node's output ports.
- `completed` — the list of completed node ids.
- `loop_counters` — how many times each loop back-edge has fired. Key these by a
  STABLE string, not the EdgeDef object: use `f"{src}->{dst}"` (a workflow has at
  most one loop back-edge per src→dst pair, so this is unambiguous).
- `stack` — the per-node trace frames (list of `{step, node, in, out}`).
- `out_live` — the collected `$output` map.

## 1. New module — `packages/flow/src/flow/checkpoint.py`

Define:

```python
from typing import Any, Protocol

class CheckpointStore(Protocol):
    """Persists and restores a run's progress snapshot, keyed by run_id."""
    def save(self, run_id: str, snapshot: dict[str, Any]) -> None: ...
    def load(self, run_id: str) -> dict[str, Any] | None: ...  # None if absent
```

And a concrete `JSONFileCheckpointStore`:

- `__init__(self, dir: str | Path)` — the directory to hold `<run_id>.json` files.
- `save` writes `<dir>/<run_id>.json` atomically (write to a temp file in the same
  dir, then `os.replace`) with `json.dump(snapshot, ...)`. Create `dir` if needed.
- `load` returns the parsed dict, or `None` if the file does not exist.

Fully type-annotated; `Protocol` from `typing`. No third-party deps.

## 2. Executor — `packages/flow/src/flow/executor.py`

Add two keyword params to `execute(...)`:

```python
    checkpoint: CheckpointStore | None = None,
    run_id: str | None = None,
```

Behaviour (only active when BOTH `checkpoint` and `run_id` are provided; otherwise
execute exactly as today):

- **Restore at start**: after building the initial `outputs` seed, call
  `snap = checkpoint.load(run_id)`. If non-None, restore `outputs`, `completed`,
  `loop_counters` (rebuild the EdgeDef→int dict from the `"src->dst"` keys — match
  each key back to the actual loop EdgeDef in `wf.edges`), `stack`, `out_live`,
  and `step_counter` (= len(stack)). A node already in `completed` must NOT run
  again: the scheduler already skips completed nodes via `_is_ready`/the pending
  set, but make sure the entry node is not re-added if it's already completed, and
  that `pending` is seeded with the not-yet-completed frontier (any node whose
  non-loop predecessors are all completed but which is itself not completed).
- **Save after each node**: add a helper `_save_checkpoint()` that builds the
  snapshot dict (the 5 fields above; serialise `loop_counters` with `"src->dst"`
  keys) and calls `checkpoint.save(run_id, snapshot)`. Call it inside the
  `_state_lock` region right after a node is recorded/completed (in `_record_frame`
  or right after `completed.add(node_id)` for both branches), so a crash loses at
  most the in-flight node.
- Do NOT change the returned `runtime` shape.

Keep it minimal and correct; the snapshot must round-trip through JSON (all values
are already strings / ints / lists / dicts).

## 3. Codegen — `packages/flow/src/flow/codegen.py` + `templates/runtime.py.tmpl`

The generated module must checkpoint the same way, driven by two env vars so the
generated `main()` needs no new arguments:

- Read `FLOW_RUN_ID` and `FLOW_CHECKPOINT_DIR` from `os.environ` at the top of
  `main()`. When both are set, enable checkpointing.
- Add module globals `_COMPLETED: set[str] = set()` and helpers in the template
  (or emitted): `_load_checkpoint()` restores `_OUT`, `_COMPLETED`, `_STACK`,
  `_OUTPUT` from `<dir>/<run_id>.json` if present; `_save_checkpoint()` writes the
  snapshot atomically. (The generated module runs nodes in a fixed order, so it
  does not need `loop_counters` for resume — a completed-node skip is enough;
  still persist `completed`/outputs/stack/output.)
- Guard each generated node call so a completed node is skipped on resume. The
  cleanest way: wrap the body of every `node_<id>` function with an early
  `if "<id>" in _COMPLETED: return`, and at the end of each node function add
  `_COMPLETED.add("<id>")` then `_save_checkpoint()`. Emit these in
  `_render_script_node` / `_render_node_function` (guarded so they are no-ops when
  checkpointing is disabled — e.g. the helpers check the env vars and return early).
- The interpret==compile tests compare the final `runtime` (state/out); checkpoint
  is a side-effect and must not change the computed result when there is no
  existing checkpoint. Verify `uv run pytest packages/flow/tests/test_codegen.py`
  and `test_integration.py` stay green.

Keep the generated module ruff-clean (add `# noqa: E501` on any long emitted line,
as the existing code does) and mypy-clean.

## 4. Tests — `packages/flow/tests/`

Add a new `test_checkpoint.py` (and you may extend `test_executor.py`):

- `JSONFileCheckpointStore` round-trips: `save` then `load` returns the same dict;
  `load` of an unknown id returns `None`; the file is written atomically (just
  assert the final file exists and parses).
- **Resume skips completed nodes**: build a 2+ node workflow. Run it once with a
  checkpoint store + run_id, but make the SECOND node raise (so the run fails after
  node 1 is checkpointed). Then re-run with the same store + run_id and a
  now-succeeding second node, and assert node 1 was NOT re-executed (e.g. a script
  node that increments a module-level counter runs only once across the two
  attempts) while node 2 completes; the final `runtime["state"]` is correct.
- Codegen parity is already covered by the existing suite; optionally add a small
  test that a generated module with `FLOW_RUN_ID`/`FLOW_CHECKPOINT_DIR` set writes
  a checkpoint file.

## 5. Gate — must be green

From the repo root, all three must pass:

```
uv run ruff check packages/flow
uv run mypy --strict packages/flow/src
uv run pytest packages/flow/tests -q
```

Run them yourself and fix anything you introduce. Do NOT weaken existing tests.
Do NOT change the `runtime` container shape or any existing public behaviour when
checkpointing is disabled (no `checkpoint`/`run_id`, no env vars) — every existing
test must pass unchanged.
