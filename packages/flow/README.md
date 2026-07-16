# flow — Multi-Agent Workflow Engine & Code Generator

`flow` lets you define multi-agent pipelines as JSON, execute them at runtime,
or compile them to a self-contained Python module.

---

## JSON Schema

```jsonc
{
  "name": "my_workflow",          // workflow identifier
  "provider": "copilot",          // ai provider id (passed to ai.provider())
  "defaults": {
    "model": "claude-sonnet-4.5"  // fallback model for nodes without model
  },
  "entry": "node_id",             // id of the first node to execute
  "state": {                      // initial key/value state (strings)
    "topic": "..."
  },
  "nodes": [
    {
      "id": "research",           // unique node identifier
      "type": "agent",            // only "agent" is supported
      "model": "...",             // optional; overrides defaults.model
      "system_prompt": "...",     // system prompt for the agent
      "prompt": "...",            // user prompt; {{key}} interpolated from STATE
      "output": "research_notes"  // STATE key where the response is stored
    }
  ],
  "edges": [
    // simple forward edge
    {"from": "research", "to": "write"},

    // conditional back-edge (loop)
    {
      "from": "review",
      "to": "write",
      "when": {"contains": {"text": "{{review_result}}", "value": "REVISE"}},
      "loop": {"max": 2}          // required for back-edges; limits iterations
    }
  ]
}
```

### Condition operators

| Operator | Shape | Meaning |
|----------|-------|---------|
| `contains` | `{"contains": {"text": "...", "value": "..."}}` | `value` is a substring of `text` |
| `equals` | `{"equals": {"text": "...", "value": "..."}}` | `text == value` |
| `not` | `{"not": <condition>}` | logical negation |
| `and` | `{"and": [<c1>, <c2>]}` | all conditions must hold |
| `or` | `{"or": [<c1>, <c2>]}` | any condition must hold |

Both `text` and `value` support `{{key}}` interpolation from STATE.

---

## CLI subcommands

### validate

Check a workflow definition for errors without executing it.

```bash
xdog-flow validate examples/research_write_review.json
# OK: research_write_review
```

### run

Execute a workflow and print the final STATE as JSON.

```bash
# Live execution using the provider declared in the JSON
xdog-flow run examples/research_write_review.json

# Override the provider from the command line
xdog-flow run examples/research_write_review.json --provider anthropic

# Offline dry-run (no LLM calls; nodes echo "DRYRUN:<model>")
xdog-flow run examples/research_write_review.json --dry-run
```

### generate

Compile the workflow to a self-contained Python module.

```bash
xdog-flow generate examples/research_write_review.json -o workflow.py
python workflow.py
```

Generated output structure:

```python
"""research_write_review — generated workflow module."""

import asyncio
import ai
from agent import Agent
from agent.core import AgentConfig, StreamFn
# ...

STATE: dict[str, str] = {"topic": "..."}

async def _run_agent(provider, model, system_prompt, prompt) -> str: ...

async def node_research(provider) -> None: ...
async def node_write(provider) -> None: ...
async def node_review(provider) -> None: ...

async def main() -> None:
    provider = ai.provider("copilot")
    await node_research(provider)
    # loop: review -> write (max 2 iterations)
    ...

if __name__ == "__main__":
    asyncio.run(main())
```

### graph

Print an ASCII topology map, a Mermaid diagram, or an SVG.

```bash
xdog-flow graph examples/research_write_review.json
# research -> write -> review --(REVISE, max 2)--> write

xdog-flow graph examples/research_write_review.json --mermaid
xdog-flow graph examples/research_write_review.json --svg > diagram.svg
```

The `--svg` output uses **Graphviz** (via `pydot` + the system `dot` binary) for
automatic layout — ranked levels, routed edges, fan-out for parallel branches,
and nodes colour-coded by type (agent vs script). If `dot` is not installed,
`to_svg` transparently falls back to a dependency-free hand-drawn renderer, so
SVG output always works (just plainer). The SVG also embeds the workflow JSON,
so it stays re-openable in `xdog-flow build` (see below).

### build

Open an interactive terminal builder to create or edit a workflow visually.

```bash
xdog-flow build my_workflow.json      # opens the TUI (creates the file if missing)
xdog-flow build my_workflow.svg       # same, but persists as an editable SVG (see below)
```

**Layout.** The builder is a **two-panel** UI. The left panel stacks three
boxed blocks — **Graph**, **Nodes**, and **Edges** — and `Tab` cycles which one
is focused (the focused box is highlighted). The right panel follows the focus:

| Focused block | Right panel shows |
|---------------|-------------------|
| **Graph** | the live ASCII flow diagram (boxed nodes + arrows/loops) |
| **Nodes** | the selected node's details (id, type, model/prompt/tools, or script `code`/`run` + typed I/O) |
| **Edges** | the selected edge as `src → dst`, its guard/loop, and the **parameter flow** (which state key the source produces and the destination consumes) |

Keys inside the builder: `Tab` switch the focused block, `a` add an agent node,
`s` add a script node, `j`/`k` (or arrows) move the selection **within the
focused block** (nodes when Nodes/Graph is focused, edges when Edges is
focused), `d` delete the focused element (the selected edge in the Edges block,
otherwise the selected node), `p` edit the selected node's prompt (type, `enter`
to commit, `escape` to cancel), `e` connect an edge (choose the destination,
`enter`), `w` save (only when the workflow is valid), `q` quit. A footer shows
the current `[mode·focus]` and a validation status line, so wiring mistakes
(unreachable inputs, loop edges missing a bound) surface as you edit. Saved
files are immediately runnable with `xdog-flow run` / `validate` / `generate`.

**SVG as an editable document.** If the path ends in `.svg`, saving writes a
**rendered diagram that also embeds the full workflow JSON** (like draw.io) — the
file is both a picture you can open in any browser AND its own source, so
`xdog-flow build my_workflow.svg` reloads and keeps editing it. The embedded
JSON (in an SVG `<metadata id="flow-workflow">` element) is the source of truth;
the drawing is derived. `xdog-flow graph <file> --svg` prints the same document.

The builder is split into a headless, fully-unit-tested core
(`flow.builder.model` + `flow.builder.actions` — every edit re-validates) and a
thin TUI shell (`flow.builder.app`). The shell — plus `flow.graph.to_svg` and
`flow.builder.svg_doc` — was **generated by a flow workflow**
(`examples/builder_codegen.json` / `examples/svg_codegen.json`: design →
implement → autofix → verify(ruff + mypy --strict + contract test) → review,
looping on failure) — flow dogfooding its own codegen against real, type-checked
targets.

---

## Declared inputs

Agent and script nodes can declare which state keys they consume via the `"inputs"` list.
These are checked **statically at validate time**: every declared input must be produced by
`state` (initial values) or a strictly earlier node.  Declaring inputs is optional but
recommended — it documents intent and catches wiring mistakes before execution.

```jsonc
{
  "id": "enrich",
  "type": "agent",
  "inputs": ["record"],            // must exist in state before this node runs
  "prompt": "Enrich:\n\n{{record}}",
  "output": "enriched"
}
```

If a key listed in `"inputs"` is not reachable from upstream, `xdog-flow validate` raises
a `WorkflowValidationError` immediately.

---

## Structured output (`output_schema`)

When a node declares `"output_schema"`, the agent **must** call the built-in `submit_result`
tool before finishing.  The executor validates the call and stores the result as a JSON string
under the node's `"output"` key.

```jsonc
{
  "id": "enrich",
  "type": "agent",
  "output": "enriched",
  "output_schema": {               // field name -> JSON type
    "category": "string",
    "token": "string",
    "summary": "string"
  }
}
```

Reading the result downstream:

```python
import json

enriched = json.loads(final_state["enriched"])
print(enriched["category"])   # "IoT / Wireless Infrastructure"
```

If the agent finishes without calling `submit_result`, the executor raises
`WorkflowExecutionError("did not submit a result")`.

---

## Script nodes

A **script node** runs a plain Python function (`def` or `async def`) instead of
an LLM agent. Its signature is **`f(ctx, <inputs by name>) -> output`**: the first
parameter is always `ctx` (a `RuntimeContext`), and each declared input arrives as
a keyword argument, coerced to its declared type. The return value is coerced back
and stored under the output name. Inputs/outputs are **typed** with JSON types
(`string`/`integer`/`number`/`boolean`/`array`/`object`).

Two code sources — a workflow is self-contained either way:

**Inline `code`** (fully decoupled — the JSON carries the function):

```jsonc
{
  "id": "add",
  "type": "script",
  "code": "def add(ctx, a, b):\n    return a + b",
  "inputs": [{"name": "a", "type": "integer"}, {"name": "b", "type": "integer"}],
  "output": {"name": "sum", "type": "integer"}
}
```
Here `state["a"]="3"`, `state["b"]="4"` are coerced to ints, so `sum` is `"7"`
(not `"34"`). See `examples/pure_script.json`.

**Ref `run`** (imports a `.py` sitting next to the workflow file — JSON + sibling
`.py` = a portable bundle; the workflow's own directory is put on `sys.path` for
the import, not the global path):

```jsonc
{ "id": "prep", "type": "script", "run": "myscript:prep",
  "inputs": [{"name": "topic", "type": "string"}], "output": {"name": "brief", "type": "string"} }
```

`ctx` exposes `ctx.state` (full state snapshot), `ctx.workflow_name`, `ctx.node_id`
for scripts that need the wider picture (e.g. config-only scripts read `ctx.state`).

**Validation** at load time: a script node sets exactly one of `code`/`run`;
inline `code` must compile and its function must be `ctx`-first with parameter
names matching the declared inputs.

> **Security:** inline `code` is `exec`'d — it runs arbitrary Python. Only load
> workflows from a trusted author. (This is a local authoring tool, not a service.)

---

## Per-node tools

Agent nodes can declare a `"tools"` list.  Each name is resolved from the
`ToolRegistry` at execution time:

```jsonc
{
  "id": "analyze",
  "type": "agent",
  "tools": ["echo"],                 // resolved via ToolRegistry
  "system_prompt": "You are an analyst.",
  "prompt": "Analyse: {{prepped}}",
  "output": "analysis"
}
```

### ToolRegistry

The executor ships a default registry pre-loaded with the `echo` built-in:

```python
from flow.tools import default_registry

registry = default_registry()
```

Register custom tools before calling `execute()`:

```python
from agent.core import AgentTool
from flow.executor import execute

my_tool = AgentTool(name="my_tool", ...)
registry = default_registry()
registry.register(my_tool)

result = await execute(wf, tool_registry=registry)
```

The generated module calls `_REGISTRY.resolve(("tool_name",))` at runtime,
so the same registry API applies to compiled workflows too.

---

## Example: auto-enrich with structured output

`examples/auto_enrich.json` demonstrates declared inputs and structured output:

- A **script node** (`pull`) that copies `state["topic"]` into `state["record"]`.
- An **agent node** (`enrich`) that declares `"inputs": ["record"]` and `"output_schema"`
  with three fields.  The agent must call `submit_result`; the executor stores the
  validated JSON under `state["enriched"]`.
- A **script node** (`persist`) that echoes `state["topic"]` into `state["saved"]`.
- Provider `copilot`, default model `claude-sonnet-4.5`.

```bash
xdog-flow validate examples/auto_enrich.json
xdog-flow run     examples/auto_enrich.json --dry-run
xdog-flow graph   examples/auto_enrich.json
```

---

## Example: script node + per-node tools

`examples/tools_script.json` demonstrates:

- A **script node** (`prep`) that calls `flow.tools:passthrough` to copy
  `state["topic"]` into `state["prepped"]`.
- An **agent node** (`analyze`) that uses the built-in `echo` tool and
  receives the prepared text via `{{prepped}}` interpolation.
- Provider `copilot`, default model `claude-sonnet-4.5`.
- Initial state: `{"topic": "workflow engines"}`.

```bash
xdog-flow validate examples/tools_script.json
xdog-flow run     examples/tools_script.json --dry-run
xdog-flow graph   examples/tools_script.json
xdog-flow generate examples/tools_script.json -o out.py
```

---

## Example: research → write → review

`examples/research_write_review.json` demonstrates:

- Three sequential agent nodes: **research**, **write**, **review**.
- A conditional back-edge: if the reviewer's output contains `REVISE`, the
  workflow loops back to **write** (at most 2 times).
- Provider `copilot`, default model `claude-sonnet-4.5`.
- Initial state: `{"topic": "the impact of large language models on software engineering"}`.

```bash
xdog-flow validate examples/research_write_review.json
xdog-flow run     examples/research_write_review.json --dry-run
xdog-flow graph   examples/research_write_review.json
xdog-flow generate examples/research_write_review.json -o out.py
```

## Example: codegen pipeline (capability demo)

`examples/codegen_builder.json` demonstrates that `flow` can orchestrate a
**code-generation pipeline** end to end with a real LLM:

```
intake (script: pop task queue)
  -> setup    (agent + bash tool: prepare an isolated working dir)
  -> design   (agent + output_schema: structured plan via submit_result)
  -> implement(agent + filesystem tool: WRITE the .py files)
  -> verify   (script: run ruff + pytest on the written files)
  -> review   (agent + output_schema: status APPROVED / FAIL)
        └─ when verdict contains "FAIL" -> loop back to implement (max 2)
```

It exercises every flow feature at once: script nodes, per-node tools
(`bash`, `filesystem`), declared `inputs`, structured `output_schema`
(the `submit_result` builtin tool), and a bounded conditional loop.

Backing code lives in `flow/codegen_tools.py`:

- `summarize_spec` / `next_task` — script-node helpers (task queue).
- `run_checks` — a script node that shells out to `ruff` + `pytest` on
  `state["target_path"]` and returns `PASS` / `FAIL: <tail>`; the `review`
  node branches on that marker to drive the loop.
- `registry_with_filesystem` — a registry with the `filesystem` + `bash` tools.
  (As of this version, `default_registry()` already includes every agent
  builtin — `bash`, `filesystem`, `submit_result`, … — so `xdog-flow run`
  resolves node `tools` out of the box.)

Run it (writes real files under the state's `target_path`):

```bash
xdog-flow validate examples/codegen_builder.json
xdog-flow graph    examples/codegen_builder.json --mermaid
xdog-flow run      examples/codegen_builder.json --provider copilot
```

**Limitations (this is an orchestration demo, not a production codegen gate):**
the flow executor runs once and returns — it has **no git isolation and no
revert-on-failure**. A `verify` FAIL only drives the bounded review→implement
loop; it cannot roll back files the `implement` node already wrote. Script
nodes are `state -> str`, so `run_checks` reports advisory text rather than
hard-gating. For gated, revertible, git-isolated code generation, use the
autobuild loop (which wraps a real ruff+mypy+pytest gate in a worktree), not a
flow workflow.
