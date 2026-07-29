"""Authored metadata for each x-dog package.

Content is hand-written (kept accurate against each package's ``pyproject.toml``
description, README, and public modules) rather than introspected at runtime, so
the site has no import-time dependency on the packages it describes.
"""

from __future__ import annotations

from dataclasses import dataclass

_GITHUB = "https://github.com/litterzhang/x-dog"


@dataclass(frozen=True)
class Package:
    """A single x-dog package as presented on the site."""

    name: str
    cli: str  # console-script command, or "" when it's a library
    tagline: str
    summary: tuple[str, ...]
    highlights: tuple[str, ...]
    install: str = ""

    @property
    def readme_url(self) -> str:
        return f"{_GITHUB}/tree/main/packages/{self.name}"


PACKAGES: tuple[Package, ...] = (
    Package(
        name="ai",
        cli="xdog-ai",
        tagline="Unified, typed LLM API",
        summary=(
            "A single, typed interface over LLM backends, built on a Provider / Protocol / Vendor "
            "split so application code never hard-codes a model SDK. The shipped vendor today is "
            "GitHub Copilot, reached over three wire protocols (openai-completions, "
            "anthropic-messages, openai-responses); the architecture is multi-vendor by design.",
            "Streaming, tool calls, web search, and token accounting are normalised behind one "
            "immutable Context / StreamOptions model.",
        ),
        highlights=(
            "One provider(id) call resolves the backend; swap models without touching app code",
            "Streaming event union, tool calls, and web search behind a typed interface",
            "Copilot auth via GitHub device code; model catalog sync with offline fallback",
            "Pure-Python message/type model — no vendor SDK leaks into your code",
        ),
        install="uv run xdog-ai --help",
    ),
    Package(
        name="agent",
        cli="xdog-agent",
        tagline="Agent runtime with tool calling and state management",
        summary=(
            "The loop that turns a model plus a set of tools into an autonomous agent: it streams "
            "the model, dispatches tool calls, applies steering, and manages conversation state "
            "and compaction.",
            "Tools are plain AgentTool objects — name, description, JSON-schema params, and an "
            "async execute — so adding a capability is a small, testable unit.",
        ),
        highlights=(
            "Tool-calling loop with parallel execution and cancellation",
            "Steering and follow-up queues for interactive control",
            "Built-in tools: filesystem, bash, current_time, web_search, submit_result",
            "Context compaction keeps long sessions within the model window",
        ),
        install="uv run xdog-agent --help",
    ),
    Package(
        name="tui",
        cli="",
        tagline="Terminal UI library with differential rendering",
        summary=(
            "A string-based component toolkit for terminal apps: components return lines, the "
            "engine diffs frames and repaints only what changed — no alternate screen, no flicker.",
            "Ships a full key-event parser (xterm + Kitty keyboard protocol) and inline image "
            "support via the Kitty and iTerm2 graphics protocols.",
        ),
        highlights=(
            "Differential line-based rendering in the main terminal buffer",
            "Rich key parsing: modifiers, Kitty protocol, backtab, function keys",
            "Inline images (Kitty / iTerm2) with pure-Python PNG/JPEG/GIF sizing",
            "Editor component, autocomplete, fuzzy match, kill-ring, keybindings",
        ),
    ),
    Package(
        name="coding",
        cli="xdog-coding",
        tagline="Interactive coding agent CLI",
        summary=(
            "A terminal coding assistant built on agent + tui: session management, a live TUI, and "
            "the filesystem/bash tools an agent needs to read and edit a real repository.",
            "It is the reference application for how the lower-level packages compose into a "
            "product.",
        ),
        highlights=(
            "Session-based interactive coding in your terminal",
            "Composes the agent runtime with the tui rendering engine",
            "Filesystem and shell tools for real repository work",
        ),
        install="uv run xdog-coding",
    ),
    Package(
        name="claw",
        cli="xdog-claw",
        tagline="Agent orchestration runtime with long-term memory",
        summary=(
            "A higher-level orchestration runtime (the NanoClaw / OpenClaw pattern) that gives "
            "agents durable, long-term memory across runs.",
            "Where agent is one loop, claw coordinates agents and the state that outlives a single "
            "conversation.",
        ),
        highlights=(
            "Long-term memory that persists across agent runs",
            "Orchestration layer above the single-agent loop",
            "Configurable runtime for multi-agent patterns",
        ),
        install="uv run xdog --help",
    ),
    Package(
        name="flow",
        cli="xdog-flow",
        tagline="Multi-agent workflow engine and JSON to Python codegen",
        summary=(
            "Define a multi-agent pipeline as JSON, run it directly, or compile it to a "
            "self-contained Python module. Data flows through named ports wired by explicit edge "
            "mappings — not a shared global state — so every connection is spelled out and "
            "statically checkable.",
            "The executor runs nodes concurrently by readiness, supports conditional and bounded "
            "loop edges, and ships an interactive TUI builder plus ASCII and SVG diagram renderers.",
        ),
        highlights=(
            "Node-private ports + explicit edge mappings (no shared flat state)",
            "Parallel fan-out/fan-in executor with conditional and loop edges",
            "Codegen: compile a workflow JSON to a runnable Python module",
            "Agent nodes with built-in web_search and JSON-declared custom tools",
            "Interactive builder TUI (xdog-flow build) with Functions/Tools viewers",
            "Deterministic ASCII flow diagrams and Graphviz-backed SVG",
        ),
        install="uv run xdog-flow --help",
    ),
)

PACKAGES_BY_NAME: dict[str, Package] = {p.name: p for p in PACKAGES}

# Layered story shown on the home page: which packages build on which.
LAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Foundation", ("ai", "tui")),
    ("Runtime", ("agent", "flow")),
    ("Products", ("coding", "claw")),
)
