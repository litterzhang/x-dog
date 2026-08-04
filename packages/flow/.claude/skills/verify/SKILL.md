---
name: verify-flow-cli
description: Verify flow runtime changes through the xdog-flow CLI and generated module surface.
---

# Verify flow through its CLI

Run from `packages/flow` with `export PATH="$HOME/.local/bin:$PATH"`.

1. Create script-only temporary workflow JSON under `mktemp -d`; avoid providers and external side effects.
2. Drive interpreter surface: `uv run xdog-flow validate workflow.json` then `uv run xdog-flow run workflow.json`.
3. Drive compiler surface: `uv run xdog-flow generate workflow.json -o generated.py` then `uv run python generated.py`.
4. Compare the two commands' printed `$output` JSON exactly.
5. Probe an adjacent invalid/error path and confirm a nonzero exit plus actionable message.

For checkpoint/resume behavior, set `FLOW_RUN_ID` and `FLOW_CHECKPOINT_DIR` when running the generated module, or use the CLI path that exposes the changed behavior. Do not substitute pytest/typecheck for runtime observation.
