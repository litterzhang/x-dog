---
title: Overview
---

*Multi-agent workflow engine and JSON to Python codegen.*

Define a multi-agent pipeline as JSON, run it directly, or compile it to a
self-contained Python module. Data flows through named ports wired by explicit
edge mappings — not a shared global state — so every connection is spelled out
and statically checkable.

The executor runs nodes concurrently by readiness, supports conditional and
bounded loop edges, and ships an interactive TUI builder plus ASCII and SVG
diagram renderers.

## Highlights

- Node-private ports + explicit edge mappings (no shared flat state)
- Parallel fan-out/fan-in executor with conditional and loop edges
- Codegen: compile a workflow JSON to a runnable Python module
- Agent nodes with built-in `web_search` and JSON-declared custom tools
- Interactive builder TUI (`xdog-flow build`) with Functions/Tools viewers
- Deterministic ASCII flow diagrams and Graphviz-backed SVG

## Try it

```bash
uv run xdog-flow --help
```

Or run a workflow live in the browser on the [HaveFun](/havefun/flow) page — load a
shipped example, fill its inputs, and watch the per-node execution log stream.
