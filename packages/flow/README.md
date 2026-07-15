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

Print an ASCII topology map or a Mermaid diagram.

```bash
xdog-flow graph examples/research_write_review.json
# research -> write -> review --(REVISE, max 2)--> write

xdog-flow graph examples/research_write_review.json --mermaid
```

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

A **script node** runs a plain async Python function instead of an LLM agent.
Set `"type": "script"` and point `"run"` at a `module.path:callable` that
accepts the current state mapping and returns a `str`:

```jsonc
{
  "id": "prep",
  "type": "script",
  "run": "flow.tools:passthrough",   // "module:async_function"
  "output": "prepped"                // STATE key where the return value is stored
}
```

The callable signature must be:

```python
async def my_fn(state: Mapping[str, str]) -> str: ...
```

`flow.tools:passthrough` (the built-in demo) returns `state.get("topic", "")`.

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
