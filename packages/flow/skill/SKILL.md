# flow — author & run workflows

Use this skill to turn a recurring, multi-step process into a **saved, replayable
workflow** and then run it. flow is a typed, single-machine workflow kernel: you
describe nodes (agent / script / human / subflow) and the typed edges between
them as JSON, then `xdog-flow run` executes it — or `xdog-flow generate` compiles
it to a self-contained Python module.

The key move: an **agent node can call a coding-agent CLI** (`claude`, `codex`)
for its step instead of a hosted provider. So this CLI can crystallize your own
workflows and run them, with each agentic step shelling back to a CLI — no API key
or provider to configure.

## When to use

- The user asks to "save / crystallize / automate this process as a workflow."
- A task is a fixed pipeline of steps (classify → route, draft → critique → revise,
  research → summarize) worth replaying deterministically around the LLM steps.
- You need typed, inspectable orchestration rather than an ad-hoc prompt chain.

## Workflow JSON at a glance

```jsonc
{
  "name": "cli-triage",
  "entry": "classify",                 // optional; else derived from $in edges
  "state": { "report": "..." },        // $in seed values (the workflow's inputs)
  "nodes": [
    {
      "id": "classify",
      "type": "agent",
      "backend": "claude-cli",         // run this step via the claude CLI (no provider)
      "model": "sonnet",               // CLI --model (alias or id); optional
      "inputs":  [{ "name": "report", "type": "string" }],
      "prompt":  "Classify:\n{{$.report}}\nFields: severity, area, summary",
      "allowed_tools": [],             // NARROW the CLI's tools; [] = none (default)
      "outputs": [                     // >1 port or a structured port => structured output
        { "name": "severity", "type": "string" },
        { "name": "area",     "type": "string" },
        { "name": "summary",  "type": "string" }
      ]
    },
    {
      "id": "route",
      "type": "script",
      "inputs": [{ "name": "severity", "type": "string" }],
      "code": "def route(ctx, severity):\n    return {'page': severity in ('high','critical')}",
      "outputs": [{ "name": "triage", "type": "object" }]
    }
  ],
  "edges": [
    { "from": "$in",       "to": "classify", "map": { "report": "report" } },
    { "from": "classify",  "to": "route",    "map": { "severity": "severity" } },
    { "from": "route",     "to": "$output",  "map": { "triage": "result" } }
  ]
}
```

Core rules:
- **Ports + edges are typed.** An edge `map` is `{ source_output_port:
  destination_input_port }`. A `{{$.x}}` in a prompt must be a declared input port.
- **$in / $output** are the reserved source/sink. `state` seeds `$in`; edges to
  `$output` collect the workflow's result.
- **Structured output**: an agent with >1 output port (or one structured/object
  port) must return an object with those fields. flow maps this to the CLI's native
  schema flag automatically — you don't write the schema.

## CLI agent nodes (the closed loop)

Set `"backend": "claude-cli"` or `"codex-cli"` on an agent node to run it via that
CLI. Then:
- **No provider needed.** A workflow whose agent nodes are all CLI-backed omits
  `provider` entirely — the CLI owns auth. (An SDK agent node, i.e. no `backend`,
  still needs a top-level `"provider"`.)
- **Tools are narrowed, not provided.** `allowed_tools` is an allow-list of the
  CLI's own tools — built-ins (`"Read"`, `"WebSearch"`) or MCP tools
  (`"mcp__github__create_issue"`). `[]` (the default) = no tools, tightest sandbox.
  flow ships no tools.
- **MCP servers (optional).** A node may declare `mcp_servers` to bring a tool the
  base CLI lacks; flow generates the CLI's MCP config for that node. Secrets use
  `${ENV_VAR}` (resolved at run time — never hardcode a token):
  ```jsonc
  "mcp_servers": { "github": { "command": "npx", "args": ["-y", "@mcp/github"],
                               "env": { "GITHUB_TOKEN": "${GITHUB_TOKEN}" } } }
  ```
- The CLI binary is found on `PATH`; override with `FLOW_CLI_BIN` (or
  `FLOW_CLI_BIN_CLAUDE_CLI` / `FLOW_CLI_BIN_CODEX_CLI`).

A workflow may **mix** SDK and CLI agent nodes and stays a normal, portable JSON
artifact — it is not locked to CLI execution.

## Other node types

- **script**: an inline `code` function `def name(ctx, *inputs) -> value/dict`, or a
  `run` ref `"module:callable"`. Deterministic; runs in-process.
- **subflow**: `"type": "subflow"` with `"subflow"` set to an inline child workflow
  object **or** a path `"./child.json"`. Its input/output ports are *derived* from
  the child's signature — don't declare them. See `examples/essay_writer.json`.
- **human**: pauses for an external signal.

Edges also support conditions (`when`: equals/contains/gt/gte/lt/lte/and/or/not),
bounded loops (`loop: {max: N}` on a back-edge), and dynamic fan-out
(`fan_out`/`fan_in`) — see the shipped examples.

## Commands

```bash
xdog-flow validate workflow.json     # fast structural + type check (do this first)
xdog-flow run      workflow.json     # execute; prints the collected $output as JSON
xdog-flow run      workflow.json --input key=value   # override a $in seed
xdog-flow generate workflow.json -o out.py           # compile to a Python module
xdog-flow graph    workflow.json     # print the ASCII diagram
```

Workflow: **write JSON → `validate` (fix any reported errors — they are precise) →
`run`.** Iterate on the JSON, not on prose.

## Examples to imitate

In `examples/`:
- `cli_triage.json` — a CLI agent node (classify) + a script router. Pure-CLI, no
  provider. The canonical shape for this skill.
- `essay_writer.json` + `essay_compose.json` — a subflow (draft→critique→revise) via
  a path reference; structured output across the boundary.
- `trip_planner.json` — typed multi-agent pipeline with JSONPath sub-field mapping.
- `agent_calculator.json` — an agent node with a tool; `refine_loop.json` — a loop.

Read the closest example, copy its shape, and adapt.
