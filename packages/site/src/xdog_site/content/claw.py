"""Deep-dive content for the ``claw`` package — agent orchestration + memory.

Accurate against packages/claw/src/claw: the gateway daemon, the orchestrator +
per-group runtimes, the concurrency model, the workspace-file system prompt,
long-term memory with optional semantic search, goals, channels (tui + weixin),
and the xdog CLI.
"""

from __future__ import annotations

from xdog_site.content.docs import Feature, PackageDocs, Phase, RefBlock, Section

_DESIGN = (
    Section(
        heading="A gateway daemon, not a one-shot process",
        body=(
            "claw runs as a long-lived gateway (core/runtime/gateway.py): a Unix-socket daemon "
            "(0600 perms) speaking a JSON-line protocol — message / reset / status / ping — that "
            "streams text deltas back to whatever is connected. It double-forks to daemonize and runs "
            "a scheduler poll loop, so agents keep working between your messages.",
        ),
    ),
    Section(
        heading="One orchestrator, many groups",
        body=(
            "The Orchestrator has a single dispatch path (route_message). Each group is an isolated "
            "GroupRuntime that owns its workspace, transcript store, memory, goals, skills, and tools. "
            "Per-group config (GroupDef) pins model, thinking level, temperature, and max tokens, so "
            "one gateway hosts several differently-tuned agents.",
        ),
    ),
    Section(
        heading="Concurrency with a semaphore and per-group locks",
        body=(
            "A global asyncio.Semaphore caps concurrent agents (default 3); each group also holds its "
            "own lock. User messages bypass the semaphore so a human is never queued behind autonomous "
            "work. Queue modes — collect / steer / steer-backlog — plus debounce and a backlog cap "
            "decide how bursts of input are folded together.",
        ),
    ),
    Section(
        heading="The system prompt is files on disk",
        body=(
            "build_system_prompt assembles a cacheable static base plus dynamic workspace overrides. "
            "The workspace holds IDENTITY.md, AGENTS.md, SOUL.md, and USER.md, a MEMORY.md snapshot, "
            "and a one-time BOOTSTRAP.md. Editing those files — or running onboard — is how you change "
            "who the agent is; IDENTITY.md's Name line is literally where the agent's name comes from.",
        ),
    ),
    Section(
        heading="Memory that outlives a conversation",
        body=(
            "MemoryManager composes a DailyLog (memory/YYYY-MM-DD.md), a long-term MEMORY.md, and "
            "search. With the search extra installed, HybridMemorySearch does vector retrieval "
            "(sentence-transformers all-MiniLM-L6-v2 + sqlite-vec) fused with BM25 via reciprocal-rank "
            "fusion; without it, a keyword SimpleMemorySearch fallback keeps memory working.",
        ),
    ),
    Section(
        heading="Goals, scheduling, and channels",
        body=(
            "A GoalManager runs a deterministic state machine plus an LLM planner with script- and "
            "agent-based verification, delivering autonomous work as SystemInput. A task scheduler "
            "handles cron / interval / one-off jobs. Channels connect the gateway to the outside: a "
            "local tui client and a weixin (WeChat) bot with long-poll monitoring and QR auth.",
        ),
    ),
)

_FEATURES = (
    Feature("Gateway daemon", "Unix-socket JSON-line server with streaming, PID/signal handling, and "
            "a scheduler loop.", "Runtime"),
    Feature("Groups", "Isolated per-group runtimes with their own workspace, memory, goals, and "
            "tools.", "Runtime"),
    Feature("Per-group config", "model_id, thinking_level, temperature, max_tokens per group.",
            "Runtime"),
    Feature("Concurrency control", "Global semaphore (default 3) + per-group locks; user messages "
            "bypass the cap.", "Runtime"),
    Feature("Queue modes", "collect / steer / steer-backlog with debounce and a backlog cap.",
            "Runtime"),
    Feature("Workspace system prompt", "IDENTITY / AGENTS / SOUL / USER .md + MEMORY snapshot + "
            "one-time BOOTSTRAP.", "Memory & prompt"),
    Feature("Long-term memory", "Daily logs + MEMORY.md persisted across runs.", "Memory & prompt"),
    Feature("Semantic search", "Optional vector + BM25 hybrid (RRF), with a keyword fallback when the "
            "search extra is absent.", "Memory & prompt"),
    Feature("Prompt caching", "Static base cached; dynamic workspace overrides applied per turn.",
            "Memory & prompt"),
    Feature("Goals & planning", "State-machine + LLM planner with script / agent verification and "
            "autonomous delivery.", "Autonomy"),
    Feature("Scheduling", "Cron, interval, and one-off tasks; daily / idle session resets.",
            "Autonomy"),
    Feature("Cross-group tools", "send_message / goal / task / todo / memory / skill tools between "
            "groups.", "Autonomy"),
    Feature("tui channel", "Local terminal client to a running gateway.", "Channels"),
    Feature("weixin channel", "WeChat bot: long-poll monitor, QR auth, typing indicators, context "
            "persistence.", "Channels"),
    Feature("Chunked delivery", "Paragraph-aware chunking streams long replies naturally.", "Channels"),
)

_FEATURE_CATEGORIES = ("Runtime", "Memory & prompt", "Autonomy", "Channels")

_REFERENCE = (
    RefBlock(
        heading="Top-level API",
        body=("Exported from claw/__init__.py.",),
        columns=("Name", "Kind", "Purpose"),
        rows=(
            ("Orchestrator", "class", "Single dispatch path over all groups."),
            ("GroupRuntime", "class", "One group's workspace, memory, goals, tools."),
            ("AgentSession / TurnResult", "class", "A running turn and its result."),
            ("MessageQueue / TaskScheduler", "class", "Input queueing and scheduled jobs."),
            ("TranscriptStore / SessionManager", "class", "Persistence of conversations."),
            ("build_system_prompt / init_workspace", "function", "Assemble the prompt / seed the "
             "workspace files."),
            ("ClawConfig / GroupDef", "type", "Global and per-group configuration."),
        ),
    ),
    RefBlock(
        heading="Workspace files",
        body=("The files under a group's workspace that shape the system prompt. Editing them (or "
              "running onboard) changes the agent.",),
        columns=("File", "Role"),
        rows=(
            ("IDENTITY.md", "Who the agent is — the Name line is the agent's name."),
            ("AGENTS.md", "Operating instructions / behavioural rules."),
            ("SOUL.md", "Persona and voice."),
            ("USER.md", "What the agent knows about the user."),
            ("MEMORY.md", "Long-term memory snapshot."),
            ("BOOTSTRAP.md", "One-time first-run setup, consumed then deleted."),
        ),
    ),
    RefBlock(
        heading="CLI — xdog",
        body=("Subcommands of the claw console script (cli/cli.py).",),
        columns=("Command", "Does"),
        rows=(
            ("onboard", "Setup wizard: provider login, model, agent name, workspace."),
            ("gateway start [--config] [--foreground]", "Start the gateway daemon."),
            ("gateway stop · gateway status", "Stop / inspect the gateway."),
            ("tui [--group]", "Attach a terminal client to a group."),
            ("channel login --weixin [--base-url]", "Authenticate the WeChat channel."),
        ),
    ),
    RefBlock(
        heading="Queue modes",
        body=("How a burst of input to one group is folded together.",),
        columns=("Mode", "Behaviour"),
        rows=(
            ("collect", "Batch pending inputs into the next turn."),
            ("steer", "Interrupt the current turn with new input."),
            ("steer-backlog", "Steer, keeping a bounded backlog of the rest."),
        ),
    ),
)

_ROADMAP = (
    Phase("Shipped", "Gateway & orchestration", (
        "Unix-socket gateway daemon with JSON-line protocol",
        "Orchestrator + isolated per-group runtimes",
        "Semaphore + per-group-lock concurrency, queue modes",
        "Cron / interval / one-off scheduling, session resets",
    ), done=True),
    Phase("Shipped", "Memory, prompt & channels", (
        "Workspace-file system prompt (IDENTITY / AGENTS / SOUL / USER)",
        "Long-term memory with optional vector+BM25 hybrid search",
        "Goals: state machine + LLM planner + verification",
        "Channels: local tui and weixin (WeChat) bot",
    ), done=True),
    Phase("2026", "More reach", (
        "Additional channels (e.g. Slack / Telegram / HTTP webhook)",
        "Multi-user groups with per-user context",
        "Richer memory consolidation and forgetting policies",
    )),
    Phase("2026", "Operability", (
        "Metrics / observability for a running gateway",
        "Hot-reload of workspace and config without restart",
        "Backup / sync of workspaces and transcripts",
    )),
)

DOCS = PackageDocs(
    name="claw",
    design_intro="How claw coordinates long-running agents with durable memory — the gateway daemon, "
                 "per-group runtimes, the file-based system prompt, and memory that outlives a run.",
    design_sections=_DESIGN,
    features_intro="What claw does today. Where agent is one loop, claw is the runtime that keeps "
                   "many agents and their state alive between conversations.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    reference_intro="The top-level API, the workspace files that shape the prompt, the xdog CLI, and "
                    "the input queue modes.",
    reference_blocks=_REFERENCE,
    roadmap_intro="Shipped foundations plus where claw is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)
