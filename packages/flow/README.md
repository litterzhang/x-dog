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
