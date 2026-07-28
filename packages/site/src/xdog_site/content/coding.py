"""Deep-dive content for the ``coding`` package — an interactive coding agent CLI.

Accurate against packages/coding/src/coding: agent+tui composition, session
management, the three run modes (interactive TUI / print / RPC), layered
settings, and the single xdog-coding command with its slash commands.
"""

from __future__ import annotations

from xdog_site.content.docs import Feature, PackageDocs, Phase, RefBlock, Section

_DESIGN = (
    Section(
        heading="The reference application for the stack",
        body=(
            "coding is where the lower-level packages become a product: it builds an agent.Agent over "
            "ai's Copilot provider and the default filesystem / bash / current_time tools, wraps it in "
            "an AgentSession, and renders it with tui. If you want to see how ai + agent + tui compose, "
            "this is the worked example.",
        ),
    ),
    Section(
        heading="Sessions that persist, branch, and compact",
        body=(
            "A session is JSON-persisted SessionData (session_manager.py). The AgentSession subscribes "
            "to Agent events to persist as it runs, compacts history when it approaches the model "
            "window (core/compaction), and supports branching — fork a conversation and restore a "
            "branch — so exploratory work does not clobber the main thread.",
        ),
    ),
    Section(
        heading="Three run modes, one core",
        body=(
            "The same session core drives three front-ends: an interactive TUI "
            "(header / chat log / status / editor) that streams tokens and visualises tool runs; a "
            "non-interactive print mode with text / json / markdown output for scripting; and an RPC "
            "mode for IDE integration.",
        ),
    ),
    Section(
        heading="Layered settings",
        body=(
            "Settings resolve session > project > global via pydantic models "
            "(settings_manager.py), so a repository can pin a model or thinking level while a single "
            "session overrides it for one run.",
        ),
    ),
    Section(
        heading="Extensible via skills and extensions",
        body=(
            "YAML skills (core/skills.py) and an extension loader (core/extensions) let a project add "
            "reusable prompts and capabilities without forking the agent.",
        ),
    ),
)

_FEATURES = (
    Feature("Interactive TUI", "Streaming chat with live tool-execution and diff display, built on "
            "the tui engine.", "Modes"),
    Feature("Print mode", "Non-interactive --print with text / json / markdown output for scripts and "
            "pipelines.", "Modes"),
    Feature("RPC mode", "A JSON-RPC front-end for IDE integration.", "Modes"),
    Feature("Piped + file input", "The initial message is assembled from --prompt, piped stdin, and "
            "file arguments.", "Modes"),
    Feature("Session persistence", "JSON-persisted sessions, resumable by most-recent or explicit id.",
            "Sessions"),
    Feature("Branching", "Fork a conversation and restore branches to explore safely.", "Sessions"),
    Feature("Compaction", "Automatic history compaction keeps long sessions within the model window.",
            "Sessions"),
    Feature("Layered settings", "session > project > global precedence via pydantic models.",
            "Sessions"),
    Feature("Default tools", "filesystem, bash, and current_time — the tools an agent needs to work a "
            "real repo.", "Tools"),
    Feature("Skills", "YAML skills add reusable prompts / capabilities per project.", "Tools"),
    Feature("Extensions", "An extension loader adds capabilities without forking the agent.", "Tools"),
    Feature("Model fallback", "Model resolution falls back sensibly when a requested model is "
            "unavailable.", "Tools"),
)

_FEATURE_CATEGORIES = ("Modes", "Sessions", "Tools")

_REFERENCE = (
    RefBlock(
        heading="CLI — xdog-coding",
        body=("A single command (not subcommands) with flags. FILES… are appended to the initial "
              "message.",),
        columns=("Flag", "Does"),
        rows=(
            ("-m / --model", "Choose the model."),
            ("-r / --resume · --resume-id", "Resume the most-recent session, or one by id."),
            ("-p / --prompt", "Provide the initial prompt."),
            ("--print · --output-format", "Non-interactive output as text / json / markdown."),
            ("--working-dir · --config", "Set the working directory / config file."),
            ("--thinking-level", "none / normal / deep / ultrathink."),
            ("--list-models · --pick-session", "List models / pick a session interactively."),
            ("--rpc · --verbose", "Start the RPC front-end / verbose logging."),
        ),
    ),
    RefBlock(
        heading="Slash commands",
        body=("Available inside the interactive session (slash_commands.py).",),
        columns=("Command", "Does"),
        rows=(
            ("/help", "List commands."),
            ("/model · /thinking", "Switch model / reasoning depth."),
            ("/compact · /clear", "Compact history / clear the session."),
            ("/session · /sessions", "Show the current session / list sessions."),
            ("/fork · /branch", "Fork the conversation / manage branches."),
            ("/quit · /exit", "Leave."),
        ),
    ),
    RefBlock(
        heading="SDK entry points",
        body=("For embedding the coding agent programmatically (core/sdk.py).",),
        columns=("Name", "Purpose"),
        rows=(
            ("create_agent_session", "Build a ready AgentSession from options."),
            ("CreateSessionOptions / Result", "Typed inputs / outputs for session creation."),
            ("AgentSession", "The persisted, event-subscribed session wrapper."),
        ),
    ),
)

_ROADMAP = (
    Phase("Shipped", "Interactive coding agent", (
        "agent + tui + ai composed into one CLI",
        "Streaming TUI with live tool-execution and diffs",
        "Default filesystem / bash / current_time tools",
        "Model fallback resolution",
    ), done=True),
    Phase("Shipped", "Sessions & integration", (
        "JSON-persisted sessions; resume by recent or id",
        "Branching and history compaction",
        "Print mode (text / json / markdown) and RPC mode for IDEs",
        "Layered session > project > global settings",
    ), done=True),
    Phase("2026", "Deeper repo awareness", (
        "Project indexing / retrieval for large codebases",
        "Test- and build-aware tool loops",
        "Richer diff review and multi-file edit flows",
    )),
    Phase("2026", "Ecosystem", (
        "Editor plugins over the RPC mode",
        "Shareable skills / extension registry",
        "Sub-agent delegation for parallel tasks (via agent)",
    )),
)

DOCS = PackageDocs(
    name="coding",
    design_intro="How coding composes ai + agent + tui into a terminal coding assistant — sessions, "
                 "three run modes, and layered settings.",
    design_sections=_DESIGN,
    features_intro="What the coding CLI does today. It is the reference application showing how the "
                   "lower-level packages fit together.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    reference_intro="The xdog-coding command, its slash commands, and the embedding SDK.",
    reference_blocks=_REFERENCE,
    roadmap_intro="Shipped foundations plus where coding is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)
