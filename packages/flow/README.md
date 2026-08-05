# Flow — Typed Workflows for Humans and Coding Agents

**Flow is a local-first, typed workflow format and compiler for developers and
Coding Agents. Design repeatable agent workflows visually or generate them with
AI, validate the JSON artifact, then run, compile, or schedule it as standalone
Python.**

Flow exists for two primary workflows:

1. **Developer authoring.** Developers compose fixed, repeatable workflows through
   the TUI today and a Web UI in the future, then run them on demand or install a
   timer/hook schedule.
2. **Agent authoring.** Claude Code, Codex, or another Agent turns a successful,
   repeatable process into a constrained `workflow.json`; `xdog-flow validate`
   supplies precise feedback so the Agent can repair it before human review.

Both paths produce the same Git-friendly artifact:

```text
Human TUI / Web UI ─┐
                     ├─> workflow.json -> validate -> run / generate / scheduling
Coding Agent + Skill ┘
```

The JSON workflow is the product's canonical intermediate representation. The
TUI/Web UI and Coding Agents are editors; the frontier executor is its interpreter;
codegen is its standalone Python backend; scheduling is an optional deployment
adapter. Flow is deliberately not a hosted low-code platform or a general-purpose
distributed workflow service.

## Product principles

- **Human/Agent symmetry:** people and Agents edit the same format.
- **Git-native:** workflows are readable files, not opaque database records.
- **Validate before execute:** ports, schemas, conditions, loops, and subflows fail
  early with actionable errors.
- **Interpret equals compile:** local execution and generated Python share one
  frontier transition kernel.
- **Local-first deployment:** no control plane is required; a workflow can become
  one Python artifact and an optional systemd schedule.
- **Fixed workflows, not open-ended autonomy:** Flow crystallizes processes that
  have become stable enough to repeat, inspect, and maintain.

---

## JSON Schema

Data flows between nodes through **named ports** wired by **explicit edge
mappings** — `nodeA.output.x -> nodeB.input.a` — not a shared global state. Each
node declares `inputs` and `outputs` port lists; an edge's `map` says which
source output port feeds which destination input port. The workflow's `state`
block seeds the output ports of a reserved source node `$in`.

```jsonc
{
  "name": "my_workflow",          // workflow identifier
  "provider": "copilot",          // ai provider id (passed to ai.provider())
  "defaults": {
    "model": "gpt-5.6-sol"  // fallback model for nodes without model
  },
  "entry": "research",            // id of the first node to execute
  "state": {                      // seed values, exposed as output ports of "$in"
    "topic": "..."
  },
  "nodes": [
    {
      "id": "research",           // unique node identifier
      "type": "agent",            // "agent" (LLM) or "script" (Python fn)
      "model": "...",             // optional; overrides defaults.model
      "system_prompt": "...",     // system prompt for the agent
      "inputs": ["topic"],        // bare string port, or {name,schema,required}
      "prompt": "Research {{topic}}",  // {{x}} reads THIS node's input port x
      "outputs": ["research_notes"]    // output ports use the same canonical forms
    }
  ],
  "edges": [
    // data edge: source output port -> destination input port
    {"from": "$in", "to": "research", "map": {"topic": "topic"}},
    {"from": "research", "to": "write", "map": {"research_notes": "research_notes"}},

    // conditional back-edge (loop). "when" reads the SOURCE node's output ports.
    {
      "from": "review",
      "to": "write",
      "when": {"contains": {"value": "{{review_result}}", "text": "REVISE"}},
      "loop": {"max": 2}          // required for back-edges; limits iterations
    }
  ]
}
```

**Ports.** A required string port may use the bare shorthand (`"topic"`). Typed,
structured, or non-required ports use the canonical object form
`{"name": "sum", "schema": {"type": "integer"}, "required": true}`. Values are
stored as native JSON/Python types and scripts receive values described by the schema.

**Validation** rejects: an input port fed by no edge mapping; a `map` referencing
a port that doesn't exist on the source/destination; and — critically — **two
unconditional edges feeding the same input port** (the ambiguous-producer clash
that a shared global state used to allow silently).

### Execution model

Both `xdog-flow run` and generated modules execute the same frontier state
machine:

1. Seed the frontier from `entry` (or all derived entry nodes).
2. Run the ready frontier concurrently in node declaration order.
3. When a node completes, evaluate its outgoing edges against that node's output.
4. A destination with ordinary predecessors waits for every predecessor to
   complete; only condition-enabled edges contribute mapped input values.
5. When no activation is ready or running, graph execution is complete.

A bounded back-edge is written `loop` or `while`; they differ only at the bound.
`loop` stops (`success: true`) and reports `context.stoppedBy` naming the edge
that ran out; `while` raises non-convergence (`success: false`, exit 1). Use
`while` when failing to converge is a failure, `loop` when the bound is a budget.
`context.lastNode` is descriptive (last node to complete, unstable under
concurrency); `stoppedBy` is the authoritative reason a run ended.

Bounded back-edges with the same destination form a conditional **AND loop
join**. All member source nodes must complete in the current generation and all
member conditions must hold before the destination runs again, exactly once.
Each edge keeps its own `max` and strict-`while` behavior, so different bounds and
mixed plain/strict members are supported.

The generated module embeds this frontier scheduler and static graph metadata;
it does not translate loops into a separate Python `for` control-flow model.

### Condition operators

| Operator | Shape | Meaning |
|----------|-------|---------|
| `contains` | `{"contains": {"value": "<haystack>", "text": "<needle>"}}` | `text` is a substring of `value` |
| `equals` | `{"equals": {"text": "...", "value": "..."}}` | `text == value` |
| `gt` / `gte` | `{"gte": {"value": "{{ $.score }}", "text": "0.8"}}` | numeric comparison |
| `lt` / `lte` | `{"lt": {"value": "{{ $.score }}", "text": "0.8"}}` | numeric comparison |
| `not` | `{"not": <condition>}` | logical negation |
| `and` | `{"and": [<c1>, <c2>]}` | all conditions must hold |
| `or` | `{"or": [<c1>, <c2>]}` | any condition must hold |

On an edge, `{{key}}` in a condition reads the **source node's output port** `key`.

---

## CLI subcommands

### validate

Check a workflow definition for errors without executing it.

```bash
xdog-flow validate examples/refine_loop.json
# OK: refine_loop
```

`--json` reports the **whole** per-node and per-edge pass in one envelope, each
error carrying the node or edge it belongs to. The prose form stops at the first
failure, which costs an authoring Agent one round trip per mistake:

```bash
xdog-flow validate broken.json --json
```
```json
{
  "ok": false,
  "path": "broken.json",
  "workflow": "refine-loop",
  "errors": [
    {"message": "Node 'critic' references unknown tool 'no_such_tool'. …", "node": "critic"},
    {"message": "Edge 'draft'->'critic': source has no output port 'x'",
     "edge": {"from": "draft", "to": "critic"}}
  ]
}
```

Exit status is 1 when `ok` is false, either way. A read or parse failure is still
a single error — there is no graph yet to say more about.

### run

Execute a workflow and print a stable result envelope containing `success`, `message`,
collected `output`, and timing/token `context`.

```bash
# Live execution using the provider declared in the JSON
xdog-flow run examples/refine_loop.json

# Override the provider from the command line
xdog-flow run examples/refine_loop.json --provider anthropic

# Offline dry-run (no LLM calls; nodes echo "DRYRUN:<model>")
xdog-flow run examples/refine_loop.json --dry-run
```

### test

Run a workflow's companion `<name>.test.json` suite. Only the boundaries a test
cannot reason about are stubbed — agent turns, human signals, whole subflow nodes
(and script nodes behind `--allow-script-stub`). Edges, conditions, loops, fan-out,
coercion and `$output` collection all run for real, because those are what the test
is for.

```bash
xdog-flow test examples/release_readiness.json --allow-script-stub
xdog-flow test examples/                      # every *.test.json under a directory
```

Stubs are injected at the provider call, so prompts are still interpolated for real
and a stubbed value is validated by the node's own output contract — the same code
that validates a live model response. No provider is ever constructed, so a suite
cannot reach the network by accident. See `docs/testing.md`.

### generate

Compile the workflow to a self-contained Python module.

```bash
xdog-flow generate examples/refine_loop.json -o workflow.py
python workflow.py
```

Generated output structure:

```python
"""refine_loop — generated workflow module."""

import asyncio
# Standalone runtime helpers + node functions are inlined here.

_OUT = {"$in": {"topic": "..."}}
_FRONTIER_SPEC = {
    "nodes": ("research", "write", "review"),
    "entries": ("research",),
    "edges": {...},
    "loop_groups": {"write": (...)},
}

async def node_research(...): ...
async def node_write(...): ...
async def node_review(...): ...

async def main() -> None:
    await _run_generated_frontier()

if __name__ == "__main__":
    asyncio.run(main())
```

The static metadata and node functions are workflow-specific; the inlined
frontier transition kernel is the same implementation used by the interpreter.

### graph

Print an ASCII topology map, a Mermaid diagram, or an SVG.

```bash
xdog-flow graph examples/refine_loop.json
# research -> write -> review --(REVISE, max 2)--> write

xdog-flow graph examples/refine_loop.json --mermaid
xdog-flow graph examples/refine_loop.json --svg > diagram.svg
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

## Declared input ports

Agent and script nodes declare their input ports via `"inputs"`. Each input port
**must be fed by an incoming edge's `map`** — checked statically at validate time.
Prompt `{{x}}` and script arguments read the node's own input port `x` (they are
port-local, not a global lookup).

```jsonc
{
  "id": "enrich",
  "type": "agent",
  "inputs": ["record"],            // input port, fed by an edge map
  "prompt": "Enrich:\n\n{{record}}",
  "outputs": ["enriched"]
}
// wired by, e.g.:  {"from": "pull", "to": "enrich", "map": {"record": "record"}}
```

If an input port is fed by no edge mapping, `xdog-flow validate` raises a
`WorkflowValidationError` immediately.

---

## Structured output (`output_schema`)

When a node declares `"output_schema"`, the agent **must** call the built-in `submit_result`
tool before finishing.  The executor validates the call and stores the result as a JSON string
in the node's (single) output port.

```jsonc
{
  "id": "enrich",
  "type": "agent",
  "outputs": ["enriched"],
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

enriched = json.loads(result.outputs["enrich"]["enriched"])
print(enriched["category"])   # "IoT / Wireless Infrastructure"
```

If the agent finishes without calling `submit_result`, the executor raises
`WorkflowExecutionError("did not submit a result")`.

---

## Script nodes

A **script node** runs a plain Python function (`def` or `async def`) instead of
an LLM agent. Its signature is **`f(ctx, <input ports by name>) -> output`**: the
first parameter is always `ctx` (a `RuntimeContext`), and each declared input port
arrives as a keyword argument, coerced to its declared type. The return value is
coerced back and stored in the node's output port (a node with multiple output
ports returns a dict keyed by port name). Ports are **typed** with JSON types
(`string`/`integer`/`number`/`boolean`/`array`/`object`).

Two code sources — a workflow is self-contained either way:

**Inline `code`** (fully decoupled — the JSON carries the function):

```jsonc
{
  "id": "add",
  "type": "script",
  "code": "def add(ctx, a, b):\n    return a + b",
  "inputs": [{"name": "a", "schema": {"type": "integer"}}, {"name": "b", "schema": {"type": "integer"}}],
  "outputs": [{"name": "sum", "schema": {"type": "integer"}}]
}
// wired by:  {"from": "$in", "to": "add", "map": {"a": "a", "b": "b"}}
```
The input ports `a`/`b` arrive as ints (from `"3"`/`"4"`), so `sum` is `"7"`
(not `"34"`). See `examples/pure_script.json`.

**Ref `run`** (imports a `.py` sitting next to the workflow file — JSON + sibling
`.py` = a portable bundle; the workflow's own directory is put on `sys.path` for
the import, not the global path):

```jsonc
{ "id": "prep", "type": "script", "run": "myscript:prep",
  "inputs": [{"name": "topic", "schema": {"type": "string"}}], "outputs": [{"name": "brief", "schema": {"type": "string"}}] }
```

`ctx` exposes `ctx.inputs` (this node's input ports as a mapping),
`ctx.workflow_name`, and `ctx.node_id`.

**Validation** at load time: a script node sets exactly one of `code`/`run`;
inline `code` must compile and its function must be `ctx`-first with parameter
names matching the declared input ports.

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
  "outputs": ["analysis"]
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

## Current examples

The checked-in examples are executable. The flat ones are mirrored into
`skill/examples/` as templates for an authoring Agent to imitate.

- `agent_calculator.json` — typed agent output and arithmetic scripting.
- `refine_loop.json` — conditional review/revision loop with a non-required
  feedback input.
- `trip_planner.json` — structured planning and nested field mappings.
- `essay_writer.json` / `essay_compose.json` — opaque subflow composition.
- `cli_triage.json` — coding-agent CLI backend.
- `digest_timer.json` / `triage_hook.json` — timer and hook scheduling.
- `release_readiness.json` / `release_report.json` — SDK-agent release radar for
  this local repository, with filesystem/bash tools, dynamic fan-out, deterministic
  scoring, a report subflow, review loop, and weekly scheduling.
- `release_readiness.test.json` / `release_report.test.json` — their test suites:
  fan-out stubs selected by input value, a loop pinned to its `loop.max` bound, and
  a whole subflow stubbed out.
- `depins_enrich/` — a **case study**, not a template: the workflow that actually
  runs unattended every four hours against a live site, writing real commits. It is
  a directory rather than a single file because it is the only example whose script
  nodes are `run:` references to sibling modules, and the only one where an Agent's
  work is admitted by a deterministic gate and repaired in a bounded `while` loop.
  See `examples/depins_enrich/README.md`.

```bash
xdog-flow validate examples/refine_loop.json
xdog-flow run examples/refine_loop.json --dry-run
xdog-flow graph examples/refine_loop.json --mermaid
xdog-flow generate examples/refine_loop.json -o workflow.py
python workflow.py
xdog-flow test examples/ --allow-script-stub
```

---

## Licence

flow is licensed under the **GNU Affero General Public License v3.0 or later**
(see [LICENSE](../../LICENSE)).

**Workflows you compile with it are not.** `xdog-flow generate` inlines parts of
flow's own runtime into its output, so without an explicit carve-out the AGPL
would follow those copied portions into every compiled workflow. The
[flow Generated Output Exception](../../LICENSE-EXCEPTION.md) grants you the
right to convey generated modules, portable bundles, scheduling units and
workflow definitions under terms of your choice, including proprietary and
commercial ones.

In short: **use it, and what it produces is yours; fork it or offer it as a
service, and share your changes.**
