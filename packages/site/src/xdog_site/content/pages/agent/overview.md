---
title: Overview
---

# agent

*Agent runtime with tool calling and state management.*

The loop that turns a model plus a set of tools into an autonomous agent: it
streams the model, dispatches tool calls, applies steering, and manages
conversation state and compaction.

Tools are plain `AgentTool` objects — name, description, JSON-schema params, and
an async `execute` — so adding a capability is a small, testable unit.

## Highlights

- Tool-calling loop with parallel execution and cancellation
- Steering and follow-up queues for interactive control
- Built-in tools: `filesystem`, `bash`, `current_time`, `web_search`, `submit_result`
- Context compaction keeps long sessions within the model window

## Try it

```bash
uv run xdog-agent --help
```
