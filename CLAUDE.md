# CLAUDE.md — x-dog Project Intelligence

> Tools for building AI agents and managing LLM deployments.
> Version: 0.57.1 | Python 3.13 | Author: Mario Zechner

## Quick Reference

```bash
# Setup
pyenv local 3.13.5 && python -m venv .venv && source .venv/bin/activate
pip install -e .

# Test
pytest                           # all packages (asyncio_mode=auto)
pytest tests/coding            # coding agent tests only
pytest tests/agent               # agent runtime tests only
pytest tests/ai                  # LLM API tests only
pytest tests/claw             # orchestrator tests only
pytest tests/tui                 # terminal UI tests only
pytest -k test_agent_tools       # single test file

# Lint & Type Check
ruff check . --line-length 120 --target-version py311
mypy src/ --strict
```

---

## Architecture Overview

```
                    +-----------+
                    | pods      |  GPU pod management (vLLM)
                    +-----------+
                         |
+--------+    +--------+    +-----------+    +----------------+
| tui    | -> | claw   | -> | agent     | -> |    ai          |
| (TUI)  |    | (orch) |    | (runtime) |    | (LLM gateway)  |
+--------+    +--------+    +-----------+    +----------------+
     |             |                              |
     |       +-----+-----+              +--------+--------+
     |       | mom       |              | Providers:      |
     |       | (Slack    |              | Anthropic       |
     |       |  bot)     |              | OpenAI          |
     |       +-----------+              | Google/Vertex   |
     |                                  | Bedrock         |
     +-- coding                        | Mistral         |
         (xdog-coding CLI)               | Copilot         |
                                       +-----------------+
```

### Data Flow (claw runtime)

```
User Input (TUI/WeChat)          System Input (scheduler/goal_runner)
         |                                |
    [UserInput]                    [SystemInput]
         |                                |
         +---------- GroupInput ----------+
                         |
                         v
[Gateway] -- Unix socket server (gateway.sock)
    |
    v
[Orchestrator] -- single dispatch path for ALL messages
    |
    +---> [MessageQueue] -- collect/steer/steer-backlog (OpenClaw pattern)
    |
    v
[SessionManager] -- get_or_create_agent_session(runtime)
    |                  daily/idle resets, caching, eviction
    v
[AgentSession] -- owns long-lived agent.Agent
    |
    +---> pre-turn: build_system_prompt(), _maybe_compact()
    +---> agent.prompt(content) -> event stream
    +---> drain events: TextDelta -> TUI, ToolExecution -> track
    +---> persist via _persisted_count (incremental, no full reload)
    +---> push-persist via Agent.subscribe(AgentEndEvent) (crash-safe)
    |
    v
[agent.Agent] -- thought-action-observation loop
    |
    +---> stream_fn via [AgentBridge] -> ai.stream()
    +---> tools: memory, filesystem, messaging, scheduling, goals
    +---> steering & follow_up queues for mid-turn control
    |
    v
[Compaction] -- pre-turn, when context nears limit:
    +---> [FlushRunner] -- silent agent turn, saves to memory
    +---> [Summarizer] -- direct LLM call via ai.stream.complete()
    +---> archive transcript to conversations/
    +---> compact transcript
    |
    v
Response -> [BlockChunker] -> [Channel] -> User
```

---

## Package Details

### 1. `ai` — Unified Multi-Provider LLM API

**Purpose**: Common streaming interface for all LLM providers.

**Key Files**:
- `types.py` — All frozen dataclasses: `Model`, `Message` (User/Assistant/ToolResult), `Context`, `Tool`, `StreamOptions`, `Usage`, streaming events (`TextDeltaEvent`, `ToolCallDoneEvent`, etc.)
- `stream.py` — High-level `stream()` and `complete()` functions
- `api_registry.py` — Provider registration system (`register_api()`, `get_api()`)
- `models.py` + `models_generated.py` — Model definitions with pricing, context windows
- `providers/` — Provider implementations:
  - `anthropic.py` — Anthropic Messages API
  - `openai_completions.py` — OpenAI Chat Completions
  - `openai_responses.py` — OpenAI Responses API
  - `google.py` — Google Generative AI
  - `google_vertex.py` — Google Vertex AI
  - `amazon_bedrock.py` — AWS Bedrock Converse
  - `mistral.py` — Mistral Conversations
  - `register_builtins.py` — Auto-registers all providers on import
- `utils/oauth/` — GitHub Copilot OAuth flow (PKCE, token management)
- `utils/event_stream.py` — Async EventStream iterator pattern
- `env_api_keys.py` — Environment variable API key resolution

**Design Pattern**: Registry-based provider dispatch. Each provider registers a `StreamFunction` that takes `(Model, Context, StreamOptions)` and returns an async iterator of `AssistantMessageEvent`.

### 2. `agent` — Agent Runtime with Tool Calling

**Purpose**: Generic thought-action-observation agent loop.

**Key Files**:
- `agent.py` — `Agent` class: high-level wrapper with state management, event subscriptions, steering/follow-up queues
- `agent_loop.py` — Core async loop: stream LLM -> extract tool calls -> execute tools -> repeat
- `types.py` — `AgentTool`, `AgentToolResult`, `AgentState`, `AgentContext`, `AgentLoopConfig`, `AgentEvent` union (12 event types)
- `event_stream.py` — `AgentEventStream`: push-based async iterator for broadcasting lifecycle events
- `proxy.py` — Remote proxy implementation via SSE

**Agent Lifecycle**:
1. `agent.prompt(message)` -> creates `UserMessage`, starts loop
2. Loop: stream LLM -> check for tool calls -> execute tools sequentially -> check steering/follow-up -> repeat
3. `agent.steer(msg)` — interrupt mid-turn (skips pending tools)
4. `agent.follow_up(msg)` — queue for after tool execution
5. `agent.abort()` — cancel via asyncio.Event

**Key Types**:
- `AgentTool` — name, description, parameters (JSON schema), async `execute(tool_call_id, params, cancel, on_update) -> AgentToolResult`
- `AgentState` — immutable snapshot: system_prompt, model, tools, messages, is_streaming, pending_tool_calls, error
- `StreamFn` — async callable that bridges Agent to any LLM backend

### 3. `claw` — AI Agent Orchestration Runtime (CORE)

**Purpose**: NanoClaw/OpenClaw-inspired orchestration layer for running AI agents as personal assistants. This is the most complex and most important package.

**Architecture** (see data flow diagram above):

#### Gateway (`gateway.py`)
- Unix socket server (`GatewayServer`)
- JSON-line protocol: `message`, `reset`, `status`, `ping`
- Streams text deltas to TUI client as they arrive
- Scheduler polling loop (30s interval) for cron/interval/one-off tasks
- Goal runner loop with cooldown (120s) for autonomous task progression
- WeChat channel integration (optional)
- PID file management, signal handling (SIGTERM/SIGINT)

#### Orchestrator (`orchestrator.py`)
- Central coordinator: channels, groups, runtimes
- `register_group()` — creates `GroupRuntime` (single `_runtimes` dict)
- `route_message()` — public entry with streaming callback support
- All messages (TUI, WeChat, scheduler, goals) go through queue
- Queue modes: collect (coalesce), steer (interrupt), steer-backlog (both)
- Chunked response delivery via `BlockChunker`
- Task scheduling and goal runner integration

#### GroupRuntime (`group_runtime.py`)
- Per-group infrastructure container (created once per `register_group()`)
- Owns: workspace, `SessionManager`, `MemoryManager`, `GoalTracker`, tools
- Resolves per-group `AgentConfig` (model_id → Model, thinking_level, temperature)
- Holds `FlushRunner` and `Summarizer` (reusable compaction helpers)
- `build_system_prompt()` — concatenates workspace identity files + goals

#### AgentSession (`agent_session.py`)
- Owns a long-lived `agent.Agent` (one per session, reused across turns)
- `run_turn()` flow:
  1. Build system prompt from workspace identity files
  2. Pre-turn compaction check (`_maybe_compact`)
  3. `agent.prompt(content)` — full tool loop
  4. Drain event stream: text deltas, tool results, usage
  5. Incremental persist (only new messages, no full reload)
- Push-based crash-safe persistence via `Agent.subscribe(AgentEndEvent)`
- `dispose()` — unsubscribe from agent events
- Session branching: `create_branch()`, `restore_branch()`, `list_branches()`
- `TurnResult` — frozen dataclass with response_text, tool_calls, error, usage

#### Agent Bridge (`agent_bridge.py`)
- `create_pi_ai_stream_fn()` — bridges agent event model to ai streaming

#### Session Manager (`session.py`)
- JSONL transcript storage per session
- Session index (sessions.json) per group
- AgentSession lifecycle: `get_or_create_agent_session(runtime)` with daily/idle resets
- Cached AgentSession instances with LRU eviction (`evict_idle()`)
- `steer()`, `follow_up()`, `abort()` — delegates to cached session
- Branch storage: `save_branch()`, `load_branch()`, `list_branches()`
- Pure check methods: `needs_daily_reset()`, `needs_idle_reset()`

#### Compaction (`compaction/`)
- `transcript.py` — `estimate_tokens()`, `compact_transcript()`, `archive_transcript()`
- `flush_runner.py` — `FlushRunner`: pre-compaction silent agent turn (uses Agent with memory tools)
- `summarizer.py` — `Summarizer`: structured summary via `ai.stream.complete()` (no Agent needed)
- Pre-turn compaction (prevents token overflow, unlike post-turn)

#### Memory System (`memory/`)
- `manager.py` — `MemoryManager` facade: composes DailyLog + LongTermMemory + Search
- `daily_log.py` — Append-only `memory/YYYY-MM-DD.md` files
- `long_term.py` — `MEMORY.md` read/write (evergreen)
- `indexer.py` — Chunks .md files -> embeds via sentence-transformers -> SQLite with sqlite-vec
- `search.py` — Hybrid pipeline: vector similarity + BM25 keyword + MMR re-rank
- `simple_search.py` — Keyword-only fallback
- `flush.py` — Prompt templates for flush and summary

#### Workspace (`workspace.py`)
- Per-group directory with identity files:
  - `AGENTS.md` — operating instructions
  - `SOUL.md` — persona, tone, boundaries
  - `IDENTITY.md` — agent name
  - `USER.md` — human description
  - `TOOLS.md` — tool usage guidance
  - `BOOTSTRAP.md` — one-time first-run (deleted after)
  - `MEMORY.md` — long-term durable memory
  - `memory/` — daily logs
  - `conversations/` — archived transcripts
- `build_system_prompt()` — concatenates all identity files
- `run_bootstrap()` — reads and deletes BOOTSTRAP.md

#### Tools (`tools/`)
- `__init__.py` — `ToolContext` dataclass + `create_all_tools(ctx)` factory
- Built-in agent tools: `bash`, `filesystem`, `grep`, `find`, `current_time`
- Claw-specific tools (all return `AgentTool`):
  - `memory_search` — hybrid semantic recall
  - `memory_get` — targeted .md file read
  - `memory_write` — append to daily log or MEMORY.md
  - `send_message` — cross-group messaging
  - `schedule_task` — create cron/interval/one-off tasks
  - `cancel_task` — remove scheduled tasks
  - `list_tasks` — list scheduled tasks
  - `read_file` — read file (workspace-scoped)
  - `write_file` — write file (workspace-scoped)
  - `list_dir` — list directory (workspace-scoped)
  - `goal_create` — create goal with tasks
  - `goal_update` — update task/goal status
  - `goal_list` — list goals
  - `todo_write` — progress tracking checklist

#### Goals (`goals.py`)
- `GoalTracker` — persistent goal/task management
- JSON persistence (goals.json per group)
- States: active/completed/abandoned (goals), pending/in_progress/completed/skipped (tasks)
- Notification queue for TUI updates
- Active summary injected into system prompt

#### Channels (`channels/`)
- `base.py` — Abstract `Channel` interface: connect, disconnect, send_message, send_chunk
- `tui_channel.py` — tui integration
- `weixin/` — WeChat channel with polling, auth, context token management

#### Other
- `config.py` — YAML config with layered resolution (defaults < file < CLI)
- `queue.py` — Lane-aware FIFO with semaphore concurrency control
- `scheduler.py` — Cron (croniter), interval, one-off scheduling
- `pruning.py` — In-memory tool result trimming
- `chunker.py` — Paragraph-aware message splitting (respects code fences)
- `cli.py` — CLI entry point
- `tui_app.py` / `tui_client.py` — TUI client connecting to gateway

### 4. `tui` — Terminal UI Library

**Purpose**: Custom terminal UI with differential rendering.

**Key Files**:
- `tui.py` — Main TUI application class
- `terminal.py` — Raw terminal I/O, ANSI escape handling
- `keys.py` / `keybindings.py` — Key event parsing and binding
- `stdin_buffer.py` — Buffered stdin reader
- `components/` — Component library:
  - `box.py`, `container.py` — Layout components
  - `editor.py`, `input.py` — Text editing with undo/redo
  - `markdown.py` — Markdown rendering
  - `image.py` — Terminal image rendering
  - `text.py`, `truncated_text.py` — Text display
  - `select_list.py` — Selection menus
  - `loader.py`, `spacer.py` — Utility components
- `autocomplete.py` / `fuzzy.py` — Fuzzy matching for autocomplete
- `kill_ring.py` / `undo_stack.py` — Emacs-style editing features

### 5. `coding` — Interactive Coding Agent CLI (`xdog-coding`)

**Purpose**: Standalone coding agent with session management, TUI interface, and slash commands. Uses `agent.Agent` as its core engine with built-in tools from `agent.tools` (bash, filesystem, grep, find).

**Key Files**:
- `main.py` — CLI entry point (`xdog-coding` command)
- `config.py` — Hierarchical config (global < project < CLI)
- `core/agent_session.py` — Main session controller with compaction and branching
- `core/compaction/` — Context compaction with LLM summarization
- `core/extensions/` — Plugin system (`~/.pi/extensions/`)
- `core/skills.py` — Slash commands from YAML
- `core/session_manager.py` — JSON-based session persistence
- `core/model_registry.py` / `model_resolver.py` — Model lookup
- `core/system_prompt.py` / `prompt_templates.py` — Prompt construction
- `core/event_bus.py` — Event dispatching
- `cli/` — CLI argument parsing, session picker, model listing
- `modes/interactive/` — TUI-based interactive mode with streaming, diff display, tool execution visualization

**Note**: The `coding` package now uses `agent.Agent` as its core engine with built-in tools from `agent.tools` (bash, filesystem, grep, find). The old per-tool classes (`core/tools/`, `core/bash_executor.py`) are legacy code pending removal.

### 6. `mom` — Slack Bot for Coding Agent

**Purpose**: Slack-connected coding agent with sandbox execution.

**Key Files**:
- `main.py` — Entry point
- `agent.py` — Agent orchestration
- `slack.py` — Slack API integration
- `sandbox.py` — Sandboxed execution environment
- `context.py` — Conversation context management
- `store.py` — Persistence
- `tools/` — Tool implementations: `attach`, `bash`, `edit`, `read`, `write`, `truncate`

### 7. `flow` — Multi-Agent Workflow Engine & Code Generator

**Purpose**: Define multi-agent pipelines as JSON, execute them at runtime, or compile them to a self-contained Python module.

**Key Files**:
- `models.py` — frozen dataclasses: `WorkflowDef`, `NodeDef`, `EdgeDef`, `Condition`
- `loader.py` — `load_workflow(path)` — parse + validate JSON into `WorkflowDef`
- `executor.py` — `execute(wf, ...)` — async thought-action-observation loop with conditional edges and bounded back-edge loops
- `codegen.py` — `generate(wf)` — emit a self-contained Python module from a workflow
- `graph.py` — `to_ascii()` / `to_mermaid()` — topology rendering
- `cli.py` — `xdog-flow` CLI: `validate`, `run` (`--timeout` per-node), `generate`, `graph` (`--mermaid`/`--svg`), `build`
- `graph.py` — `to_ascii` / `to_mermaid` / `to_svg` topology rendering. `to_svg` uses Graphviz auto-layout (via `pydot` + system `dot`), colour-coded by node type, with a dependency-free `_to_svg_fallback` when `dot` is absent.
- `builder/` — visual workflow builder: `serialize.py` (workflow↔JSON round-trip), `model.py` + `actions.py` (headless, fully-tested edit core, re-validates on every edit), `app.py` (TUI shell — 3 modes: normal/prompt-edit/edge), `svg_doc.py` (SVG-as-editable-document: embeds workflow JSON in an `<metadata id=flow-workflow>` element so a saved `.svg` is both a diagram and its own source), `io.py` (`.svg`/`.json` load/save dispatch). `app.py` + `to_svg` + `svg_doc` were **generated by flow workflows** (`examples/builder_codegen.json`, `examples/svg_codegen.json`). `xdog-flow build <file.json|file.svg>` opens it.
- `examples/research_write_review.json` — 3-node research→write→review with conditional loop
- `examples/tools_script.json` — script node + per-node tools demo
- `examples/auto_enrich.json` — declared inputs + structured output via submit_result demo
- `examples/codegen_builder.json` — codegen pipeline demo (design→implement→verify→review loop; script+bash+filesystem+submit_result); `codegen_tools.py` backs it. Orchestration demo only — no git isolation/revert (use the autobuild loop for gated codegen).
- `tools.py` — `ToolRegistry` (register/resolve `AgentTool` by name) + `default_registry()` (now includes agent builtins: bash/filesystem/submit_result/…) + `passthrough` script-node function

**Design**: Each node is an `agent.Agent` turn (type `"agent"`) or a Python function (type `"script"`). A script node's function is `f(ctx, <inputs by name>) -> output` with typed inputs/output (JSON types coerced to/from the string state); its code is either **inline** (`"code"` field, exec'd — fully self-contained JSON) or a **ref** (`"run": "module:func"` imported from the workflow file's own directory). `ctx` is a `RuntimeContext` (state/workflow_name/node_id). Inline code is validated at load (compile + ctx-first signature matching declared inputs). Edges are walked after each node; back-edges (loops) require `loop.max` and are bounded at runtime. State is a flat `dict[str, str]`; `{{key}}` in prompts is interpolated from state. Provider is resolved once via `ai.provider()` and shared across all nodes. Per-node `"tools"` lists are resolved via `ToolRegistry`. Agent nodes support `"inputs": [keys]` (reachability-checked at validate time) and `"output_schema": {field: type}` (forces the agent to call the `submit_result` builtin tool; validated JSON stored under the node's output key).

---

### 8. `pods` — GPU Pod Management CLI

**Purpose**: CLI for managing vLLM deployments on GPU pods.

**Key Files**:
- `cli.py` — CLI entry point
- `config.py` — Pod configuration
- `ssh.py` — SSH connection management
- `model_configs.py` — vLLM model configurations
- `commands/` — CLI commands: `pods` (manage), `models` (list), `prompt` (run)

---

## Data Directory Layout (claw)

```
data/
  groups/
    <groupId>/
      workspace/           # Agent workspace (identity + memory)
        AGENTS.md          # Operating instructions
        SOUL.md            # Persona, tone, boundaries
        IDENTITY.md        # Agent name
        USER.md            # Human description
        TOOLS.md           # Tool usage guidance
        BOOTSTRAP.md       # One-time first-run (deleted after)
        MEMORY.md          # Long-term durable memory
        memory/            # Daily logs (YYYY-MM-DD.md)
        conversations/     # Archived transcripts (.md)
      sessions/
        sessions.json      # Active sessions index
        <sessionId>.jsonl  # Conversation transcripts
      goals.json           # Persistent goals and tasks
      memory.db            # SQLite + sqlite-vec (if hybrid search enabled)
  scheduled_tasks.json     # Cron/interval/one-off task definitions
```

---

## Code Conventions

- **Immutability**: All core types are `@dataclass(frozen=True)`. State updates use `dataclasses.replace()`.
- **Async**: asyncio throughout. No blocking I/O in agent loop.
- **Type Unions**: Discriminated unions via `Literal` type fields (e.g., `AgentEvent`, `AssistantMessageEvent`).
- **Tool Pattern**: All tools return native `AgentTool`. `ToolContext` dataclass holds shared dependencies; `create_all_tools(ctx)` assembles them.
- **Testing**: pytest + pytest-asyncio (asyncio_mode=auto). 192 tests for claw, 579 total.
- **Config**: YAML-based with layered resolution. Sensitive keys (api_key, tokens) never written to disk.
- **Logging**: Standard `logging` module throughout.

## Key Design Decisions

1. **In-process asyncio** (not Docker/subprocess) — matches OpenClaw's embedded agent pattern
2. **JSONL** for session transcripts — simple, append-only, human-readable
3. **Workspace = agent brain** — markdown files are source of truth for identity + memory
4. **agent.Agent directly embedded** — long-lived per session with event subscription for persistence
5. **Local embeddings** (sentence-transformers) — zero API cost, offline capable
6. **Queue modes** from OpenClaw — collect/steer/steer-backlog for mid-run message handling
7. **Pre-turn compaction** — prevents token overflow; FlushRunner + Summarizer as explicit entities
8. **Gateway daemon** — long-running process with Unix socket, TUI connects as client
9. **Goal runner** — background autonomous task progression with cooldown
10. **SessionManager owns AgentSession lifecycle** — creation, resets, caching, eviction in one place
11. **Single dispatch path** — gateway always routes through orchestrator queue

## Current Status

- **All feature areas** fully implemented in claw
- **Implemented**: Long-lived Agent with event subscription, push-based persistence, pre-turn compaction, session branching, LRU eviction, ToolContext, MemoryManager facade
- **Not implemented**: Memory vector search (embeddings, sentence-transformers, sqlite-vec, BM25) — uses SimpleMemorySearch keyword fallback
- **All 192 claw tests passing (579 total)**
