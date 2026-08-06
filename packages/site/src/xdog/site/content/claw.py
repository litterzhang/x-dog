"""Dynamic (Features + Roadmap) content for the ``claw`` package.

claw's static pages (Overview / Design / Reference) are markdown under
``content/pages/claw/``; its Features and Roadmap stay in Python here. Accurate
against packages/claw/src/claw: the gateway daemon, the orchestrator + per-group
runtimes, the concurrency model, the workspace-file system prompt, long-term
memory with optional semantic search, goals, channels (tui + weixin), and the
xdog CLI.
"""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

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
    features_intro="What claw does today. Where agent is one loop, claw is the runtime that keeps "
                   "many agents and their state alive between conversations.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    roadmap_intro="Shipped foundations plus where claw is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)
