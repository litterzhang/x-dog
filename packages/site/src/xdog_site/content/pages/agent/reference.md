---
title: Reference
---

The top-level API, the built-in tools, and the `xdog-agent` CLI.

## Top-level API

The names exported from `agent/__init__.py` that applications use directly.

| Name | Kind | Purpose |
|---|---|---|
| `Agent` | class | Stateful wrapper: state, events, steering / follow-up queues. |
| `agent_loop / run_agent_loop` | function | The raw tool-calling loop and its runner. |
| `AgentConfig` | type | Model, system prompt, execution mode, limits. |
| `AgentTool` | type | name + description + JSON-schema params + async execute. |
| `AgentToolResult` | type | A tool's return payload. |
| `StreamFn / EmbedFn / WebSearchFn` | type | Protocols bridging to the model backend. |
| `ToolDef / @action / Param` | api | Declarative multi-action tool framework. |

## Built-in tools

Shipped in `agent/tools/`. `bash`, `filesystem`, `current_time`, and
`submit_result` auto-register; `web_search` and `embed` are injected from the
provider.

| Tool | Actions / purpose |
|---|---|
| `filesystem` | read · write · delete · edit · ls · grep · find |
| `bash` | Run a shell command |
| `current_time` | Return the current time |
| `submit_result` | Schema-validated structured output |
| `web_search` | Provider-backed web search (injected) |
| `embed` | Provider-backed embedding (injected) |

## CLI — `xdog-agent`

Subcommands of the agent console script.

| Command | Does |
|---|---|
| `login` | Authenticate the Copilot backend (GitHub device code). |
| `chat [model] [msg]` | Interactive or one-shot chat with tools loaded (`-s`, `-t`, `--max-tokens`, `--thinking`, `--no-tools`, `--tool-ctx`…). |

## Chat slash commands

Available inside `xdog-agent chat`.

| Command | Does |
|---|---|
| `/model` | Switch model. |
| `/thinking` | Set reasoning depth. |
| `/image` | Attach an image. |
| `/tools` | List loaded tools. |
| `/status` | Show session token / cost totals. |
| `/clear · /verbose · /help · /exit` | Session controls. |
