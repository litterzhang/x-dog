# x-dog auto-enrich

A small, reusable **dogfooding harness**: it runs the `flow` engine on a workflow
whose BUILD agent implements a task *inside this very repo*, gated by the same
checks a human would run before committing.

```
build ──▶ scope ──▶ gate ──▶ validate
(agent)   (script)  (script)  (agent)
```

- **build** — an agent (`filesystem` + `bash`, read-only shell) implements the
  task md, editing files under the allowed paths.
- **scope** — collects the in-scope `git diff`, reverting out-of-scope *tracked*
  edits. It NEVER deletes untracked files.
- **gate** — runs `uv run ruff check`, `mypy --strict`, and `pytest` on
  `packages/flow`; returns `PASS` or `FAIL: <reason>`.
- **validate** — an agent judges whether the diff genuinely implements the task.

The workflow itself never touches git history. The **driver** owns the
deterministic side-effects: a working-tree precheck, and — only when gate passes
AND the validator approves — a **local commit** (it never pushes). Any rejection
reverts the in-scope working tree (tracked edits only; never `git clean`).

## Run

```bash
# dry-run: full pipeline, but revert instead of committing
AE_DRY_RUN=1 uv run python tools/autoenrich/driver.py --task tools/autoenrich/tasks/p1_retry.md

# real run: local commit on success (review the commit, then push yourself)
uv run python tools/autoenrich/driver.py --task tools/autoenrich/tasks/p1_retry.md
```

The in-scope tree must be clean before a run (the driver needs to attribute the
diff). By default the agent may only touch `packages/flow/`; override with
`--allow "packages/foo/ packages/bar/"` or `AE_ALLOW_PATHS`.

## Safety

Both `scope` and the driver only ever **revert tracked edits** under the
allow-list (`git checkout -- <allow>`). Neither runs `git clean` nor deletes
untracked files, so un-committed work anywhere in the tree — including this
scaffold before it is committed — is never destroyed.

## Files

| File | Role |
|------|------|
| `enrich.json` | the flow workflow (build → scope → gate → validate) |
| `nodes.py` | the `scope` / `gate` script-node entry points |
| `driver.py` | precheck, run, decide, local-commit/revert |
| `prompts/builder.md` | system/instructions injected into the build agent |
| `prompts/validator.md` | instructions for the validate agent |
| `tasks/*.md` | one task spec per run (e.g. `p1_retry.md`) |

## Add a task

Drop a markdown file in `tasks/` describing exactly what to implement (be
specific: files, shapes, validation, tests, and the gate that must stay green),
then pass it with `--task`. `p1_retry.md` is a worked example (roadmap P1).
