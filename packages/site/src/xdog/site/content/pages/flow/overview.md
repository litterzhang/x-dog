---
title: Overview
---

*Typed workflows for humans and Coding Agents.*

Flow is a local-first workflow format and compiler. A developer can compose a
fixed, repeatable Agent workflow in the TUI today (and a Web UI in the future),
while Claude Code, Codex, or another Agent can generate the same constrained
`workflow.json`, run validation, repair precise errors, and present a Git diff for
human review.

```text
Human TUI / Web UI ─┐
                     ├─> workflow.json -> validate -> run / generate / scheduling
Coding Agent + Skill ┘
```

The JSON file is the canonical intermediate representation. Editors do not own a
second database model. The loader is the compiler front-end; the frontier runtime
is the interpreter; codegen is the standalone Python back-end; scheduling is an
optional local deployment adapter.

## Why Flow exists

Flow is not trying to be another open-ended Agent runtime or hosted low-code
platform. It crystallizes processes that have become stable enough to repeat,
inspect, schedule, and maintain:

- **Human/Agent symmetry** — people and Agents edit the same artifact.
- **Git-native workflows** — readable files, code review, history, and rollback.
- **Validate before execute** — ports, schemas, conditions, loops, fan-out, and
  subflows fail early with actionable errors.
- **Interpret equals compile** — direct execution and generated Python embed the
  same frontier transition kernel.
- **Local-first deployment** — no control plane is required; generate one Python
  artifact or install a systemd timer/hook.
- **Coding Agents as first-class nodes** — use the in-process SDK or invoke Claude
  Code/Codex CLI with tool and MCP declarations.

## Highlights

- Node-private JSON Schema ports and explicit edge mappings
- SDK agent, CLI agent, script, human, and opaque subflow nodes
- Conditional edges, heterogeneous bounded-loop joins, and dynamic fan-out/fan-in
- Coherent frontier-batch checkpoint/resume and deterministic memoization
- Structured success/failure result envelope with timing and token context
- Standalone, Ruff-clean Python code generation
- Interactive TUI builder plus ASCII, Mermaid, Graphviz SVG, and embedded SVG docs
- Timer and event-hook scheduling through `xdog-flow scheduling`
- Companion `*.test.json` suites via `xdog-flow test` — stub the model, run the graph

## Flagship demo — Flow Release Radar

The Release Radar audits this local xdog repository every Monday:

```text
collect_repo (deterministic script)
    -> plan_checks (SDK Agent + filesystem/bash)
    -> audit × N (dynamic fan-out SDK Agents)
    -> score_risk (deterministic policy)
    -> report subflow (compose -> critique -> revise loop)
    -> structured $output
```

It demonstrates the product idea end to end: an AI-authored, human-reviewable JSON
workflow uses typed Agent and script steps, compiles to Python, and can be installed
as a local schedule.

```bash
uv run xdog-flow validate packages/flow/examples/release_readiness.json
uv run xdog-flow graph packages/flow/examples/release_readiness.json --mermaid
uv run xdog-flow generate packages/flow/examples/release_readiness.json -o release_readiness.py
uv run xdog-flow scheduling install packages/flow/examples/release_readiness.json --dry-run
```

## Try it

```bash
uv run xdog-flow --help
```

Or run a workflow live in the browser on the [HaveFun](/havefun/flow) page. Load a
shipped example, fill its inputs, and watch the execution log.
