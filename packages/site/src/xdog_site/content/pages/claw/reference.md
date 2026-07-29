---
title: Reference
---

The top-level API, the workspace files that shape the prompt, the `xdog-claw` CLI, and
the input queue modes.

## Top-level API

Exported from `claw/__init__.py`.

| Name | Kind | Purpose |
|---|---|---|
| `Orchestrator` | class | Single dispatch path over all groups. |
| `GroupRuntime` | class | One group's workspace, memory, goals, tools. |
| `AgentSession / TurnResult` | class | A running turn and its result. |
| `MessageQueue / TaskScheduler` | class | Input queueing and scheduled jobs. |
| `TranscriptStore / SessionManager` | class | Persistence of conversations. |
| `build_system_prompt / init_workspace` | function | Assemble the prompt / seed the workspace files. |
| `ClawConfig / GroupDef` | type | Global and per-group configuration. |

## Workspace files

The files under a group's workspace that shape the system prompt. Editing them (or
running `onboard`) changes the agent.

| File | Role |
|---|---|
| `IDENTITY.md` | Who the agent is — the Name line is the agent's name. |
| `AGENTS.md` | Operating instructions / behavioural rules. |
| `SOUL.md` | Persona and voice. |
| `USER.md` | What the agent knows about the user. |
| `MEMORY.md` | Long-term memory snapshot. |
| `BOOTSTRAP.md` | One-time first-run setup, consumed then deleted. |

## CLI — `xdog-claw`

Subcommands of the claw console script (`cli/cli.py`).

| Command | Does |
|---|---|
| `onboard` | Setup wizard: provider login, model, agent name, workspace. |
| `gateway start [--config] [--foreground]` | Start the gateway daemon. |
| `gateway stop · gateway status` | Stop / inspect the gateway. |
| `tui [--group]` | Attach a terminal client to a group. |
| `channel login --weixin [--base-url]` | Authenticate the WeChat channel. |

## Queue modes

How a burst of input to one group is folded together.

| Mode | Behaviour |
|---|---|
| `collect` | Batch pending inputs into the next turn. |
| `steer` | Interrupt the current turn with new input. |
| `steer-backlog` | Steer, keeping a bounded backlog of the rest. |
