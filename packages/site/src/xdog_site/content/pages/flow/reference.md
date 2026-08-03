---
title: Reference
---

The complete workflow JSON schema, the type system, condition operators, the
runtime container a run returns, the `xdog-flow` CLI, and every check that runs
at load time. A workflow is one JSON object executed directly by the runtime or
compiled to Python — both agree field-for-field.

## JSON schema

### Workflow (top level)

A workflow is one JSON object. Nothing is strictly required, though a useful
workflow declares nodes and edges. `entry` is optional — when omitted, the
run starts from every node that depends only on `$in` (multi-entry, run in
parallel).

| Field | Required | Meaning |
|---|---|---|
| `name` | default `""` | Human-readable workflow name; shown in diagrams and the runtime container. |
| `provider` | default `""` | LLM provider id used to build the default agent stream (e.g. copilot). |
| `entry` | optional | Id of the first node to run; when unset, derived from the `$in` frontier (multi-entry). |
| `defaults.model` | default `""` | Fallback model for any agent node that does not set its own model. |
| `state` | default `{}` | Object of initial values, exposed as the output ports of the `$in` source. |
| `nodes` | default `[]` | The list of node objects (see below). |
| `edges` | default `[]` | The list of edge objects wiring node ports together. |
| `tools` | default `{}` | Custom-tool manifest: `{tool_name: "module.path:callable"}`. |

### Node

A node is either an agent (calls an LLM) or a script (runs Python). `type`
defaults to agent. An agent node must not set `code` or `run`; a script node
must set exactly one of them.

| Field | Required | Meaning |
|---|---|---|
| `id` | required | Unique node id. `$in` and `$output` are reserved and rejected. |
| `type` | default `"agent"` | `"agent"`, `"script"`, `"human"`, or `"subflow"`. |
| `model` | optional | Agent only: overrides `defaults.model` for this node. |
| `system_prompt` | default `""` | Agent only: system prompt; `{{ $.port }}` reads this node's inputs. |
| `prompt` | default `""` | Agent only: user prompt; `{{ $.port }}` is a JSONPath into this node's inputs. |
| `tools` | default `[]` | Agent only: names of built-in or manifest tools to expose. |
| `web_search` | default `false` | Agent only: enable the built-in `web_search` tool. |
| `web_search_model` | optional | Agent only: a distinct browsing model for `web_search`. |
| `code` | optional | Script only: inline source defining exactly one ctx-first function. |
| `run` | optional | Script only: a `"module.path:callable"` reference imported at run time. |
| `subflow` | optional | Subflow only: the child workflow — an inline object or a `"./child.json"` path string. Ports are derived from the child's signature; do not declare `inputs`/`outputs`. |
| `inputs` | default `[]` | Input ports (bare name, `{name, type, required}`, or `{name, schema, required}`). |
| `outputs / output` | default `[]` | Output ports; `output` is singular sugar for one port. For an agent, >1 port (or one non-string port) makes it a structured `submit_result` node — the schema is derived from the ports. |

### Port

A port is a bare string (a required `string` port) or an object. A structured
output port lets an agent fan its `submit_result` result across several ports and
lets an edge map a sub-field with a type check.

| Field | Required | Meaning |
|---|---|---|
| `name` | required | Port name; referenced by edge maps and `{{ $.name }}` interpolation. |
| `type` | default `"string"` | Shorthand for a scalar schema — one of string, integer, number, boolean, array, object. |
| `schema` | optional | A JSON Schema fragment (scalar or nested `{type, properties, items, required}`); an alternative to `type` for structured ports. |
| `required` | default `true` | Input only: `false` exempts it from the must-be-fed rule (loop-carried values); replaces the old `optional`. |

### Edge

An edge moves data from a source node's output ports to a destination node's
input ports. An empty map is a pure control edge (ordering only). `$in` is
source-only; `$output` is sink-only.

| Field | Required | Meaning |
|---|---|---|
| `from` | required | Source node id, or the reserved `$in` source. |
| `to` | required | Destination node id, or the reserved `$output` sink. |
| `map` | default `{}` | `{source_output_port: destination_input_port}` pairs. A source key may be a JSONPath into a structured port, e.g. `"$.verdict.within_budget"`. |
| `when` | optional | A condition; the edge only fires (or feeds) when it holds. |
| `loop.max` | optional | Marks a bounded back-edge; required when `to` is not strictly after `from`. |

## Type system

Every port carries a JSON type (via `type` shorthand or a full `schema`). The
wire format is **type-native**: a port value is the live Python value — a script
node reads its inputs as that value and returns the value directly, with no
stringify/parse round-trip. An edge is type-checked at load time: the source and
destination port types must match (a JSONPath sub-field is checked against the
source schema when it can be resolved). `$in` seed values are untyped, so edges
out of `$in` are exempt.

| Type | Python value | Empty → |
|---|---|---|
| `string` | `str` | `""` |
| `integer` | `int` | `0` |
| `number` | `float` | `0.0` |
| `boolean` | `bool` (true/1/yes/on → true) | `false` |
| `array` | `list` | `[]` |
| `object` | `dict` | `{}` |

## Interpolation & conditions (JSONPath)

A prompt or a condition operand may embed `{{ <jsonpath> }}` placeholders,
resolved against the node's inputs (or, for an edge `when`, the source node's
output ports): `{{ $.topic }}` for a whole port, `{{ $.plan.tasks[0] }}` for a
nested field. An unresolved path yields the empty string. The same jsonpath-ng
evaluator runs in both the interpreter and the generated module.

## Condition operators

An edge's `when` is a condition tree evaluated against the source node's output
ports. `value` and `text` support `{{ $.port }}` JSONPath interpolation.

| Op | JSON | Holds when |
|---|---|---|
| `equals` | `{"equals": {"value": V, "text": T}}` | interpolate(value) == interpolate(text) |
| `contains` | `{"contains": {"value": V, "text": T}}` | interpolate(text) in interpolate(value) |
| `gt` / `gte` / `lt` / `lte` | `{"gte": {"value": V, "text": T}}` | numeric compare: float(value) ≥ float(text) (an empty operand is lenient-false; a non-numeric one errors) |
| `not` | `{"not": <cond>}` | negation of one child condition |
| `and` | `{"and": [<cond>, ...]}` | all children hold |
| `or` | `{"or": [<cond>, ...]}` | any child holds |

Every `{{ $.key }}` operand root is checked at load time against the source node's
output ports (strict interpolation), so a typo fails validation.

## Runtime container

`execute()` returns one container describing the whole run. The CLI prints `out`
by default, falling back to the full container when a workflow declares no
`$output`.

| Key | Contents |
|---|---|
| `ctx` | The last node to run: `{step, node_id, workflow_name}`. This is also what a script node receives. |
| `stack` | A per-node delta trace: one `{step, node, in, out}` frame per run; a looped node appears once per pass. |
| `state` | Real-node outputs only: `{node_id: {port: value}}` — excludes `$in` and `$output`. |
| `in` | The `$in` seed: the workflow's state, with any run-time input overrides applied. |
| `out` | The `$output` map: the key/value pairs collected from edges targeting `$output`. |
| `failed` | Isolated failures: `{node_id: error}` for branches captured by `on_error:isolate`. |
| `memo` | The determinism ledger: `memo_key(node, input hash)` → output ports, for deterministic reuse. |
| `tokens_used` | Cumulative agent tokens spent this run; enforced against `execute(max_tokens=…)`. |

## CLI — `xdog-flow`

Every subcommand accepts a `.json` workflow or a `.svg` with the JSON embedded.

- **`xdog-flow validate <config>`** — Load and validate a workflow; prints OK or the first error.
- **`xdog-flow run <config>`** — Execute a workflow and print its `$output` (or the whole runtime container when none is declared).
  - `--dry-run` — Inject a stub LLM; agent nodes echo `DRYRUN:<model>` so you can test wiring offline.
  - `--input K=V` — Seed or override a `$in` value (repeatable; split on the first `=`).
  - `--provider X` — Override the AI provider.
  - `--timeout N` — Per-node wall-clock timeout in seconds (default 120).
  - `-v / --verbose` — Show flow's DEBUG logs — node execution and loop firing.
- **`xdog-flow generate <config>`** — Compile the workflow to a standalone Python module.
  - `-o / --output FILE` — Write to a file instead of stdout.
  - `--portable -o DIR` — Emit a self-contained bundle (vendored `ai`/`agent`, pinned deps); `--offline` also downloads wheels for a no-network install.
- **`xdog-flow graph <config>`** — Render the workflow graph.
  - `--mermaid` — Emit a Mermaid flowchart.
  - `--svg` — Emit an SVG document with the workflow JSON embedded.
- **`xdog-flow build <config>`** — Open the interactive TUI builder (created if the file is missing).

### Generated-module run-time overrides

A generated module reads a few environment variables at run time, so a compiled
workflow (or a `--portable` bundle run with `python .`) stays overridable without
regenerating — parity with `run`'s flags:

- `FLOW_INPUTS='{"days": 2}'` — a JSON object merged per-key into `$in` (mirrors `--input`).
- `FLOW_PROVIDER=openai` — override the provider (mirrors `--provider`).
- `FLOW_MAX_TOKENS=100000` — abort once cumulative agent tokens pass the ceiling.
- `FLOW_RUN_ID` + `FLOW_CHECKPOINT_DIR` — enable checkpoint/resume.

## Validated before it runs

Loading a workflow runs every check below; any failure raises before a single
node executes. This is what lets diagrams and codegen trust the graph.

| Rule | Triggered by |
|---|---|
| Node id must be non-empty | A node object has an empty id. |
| Node id `$in` / `$output` is reserved | A real node tries to claim a reserved id. |
| Duplicate node ids | Two nodes share the same id. |
| Entry node not found in nodes | `entry` names a node that does not exist. |
| Agent node must not set `run` / `code` | An agent node carries script-only fields. |
| Script must set exactly one of `code` or `run` | A script node sets both or neither. |
| Script `run` must match `module.path:callable` | A malformed run reference. |
| Script inline code invalid / must define one function | Inline code fails to parse or has ≠1 function. |
| Script function's first parameter must be `ctx` | The inline function signature does not start with ctx. |
| Script params != declared inputs | The function parameters and the declared input ports disagree. |
| References unknown tool | A node names a tool that is neither a built-in nor in the manifest. |
| Tool manifest ref must match `module.path:callable` | A malformed custom-tool reference. |
| Edge src `$output` / dst `$in` not allowed | Using the sink as a source, or the source as a sink. |
| Edge src / dst not found in nodes | An edge names an endpoint that does not exist. |
| Source/destination has no such port | An edge map names a port a node doesn't declare. |
| Back-edge must have `loop.max >= 1` | A back-edge (dst not strictly after src) is not a bounded loop. |
| Input port is fed by N unconditional edges | Two always-on producers target one input (ambiguous). |
| Input port is not fed by any edge mapping | A `required` input port has no feeder. |
