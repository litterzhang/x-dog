"""Dynamic (Features + Roadmap) content for the ``coding`` package.

coding's static pages (Overview / Design / Reference) are markdown under
``content/pages/coding/``; its Features and Roadmap stay in Python here. Accurate
against packages/coding/src/coding: agent+tui composition, session management, the
three run modes (interactive TUI / print / RPC), layered settings, and the single
xdog-coding command with its slash commands.
"""

from __future__ import annotations

from xdog_site.content.docs import Feature, PackageDocs, Phase

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
    features_intro="What the coding CLI does today. It is the reference application showing how the "
                   "lower-level packages fit together.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    roadmap_intro="Shipped foundations plus where coding is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)
