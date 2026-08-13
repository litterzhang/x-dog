"""Authored metadata for each xdog package.

Content is hand-written (kept accurate against each package's ``pyproject.toml``
description, README, and public modules) rather than introspected at runtime, so
the site has no import-time dependency on the packages it describes.
"""

from __future__ import annotations

from dataclasses import dataclass

_GITHUB = "https://github.com/litterzhang/xdog"


@dataclass(frozen=True)
class Package:
    """A single xdog package as presented on the site."""

    name: str
    cli: str  # console-script command, or "" when it's a library
    tagline: str
    summary: tuple[str, ...]
    highlights: tuple[str, ...]
    install: str = ""

    @property
    def readme_url(self) -> str:
        return f"{_GITHUB}/tree/main/packages/{self.name}"

    @property
    def dist(self) -> str:
        """The name to `pip install` — derived, so it cannot drift from reality."""
        return f"xdog-{self.name}"

    @property
    def pypi_url(self) -> str:
        return f"https://pypi.org/project/xdog-{self.name}/"


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
        install="pip install xdog-ai",
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
        install="pip install xdog-agent",
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
        install="pip install xdog-coding",
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
        install="pip install xdog-claw",
    ),
    Package(
        name="flow",
        cli="xdog-flow",
        tagline="Typed workflows for humans and Coding Agents",
        summary=(
            "A local-first workflow format and compiler: developers design repeatable Agent "
            "workflows in the TUI (and future Web UI), while Coding Agents generate the same "
            "Git-friendly JSON artifact and repair it through precise validation feedback.",
            "Run the JSON directly, compile it to standalone Python, or install a local timer/hook "
            "schedule. Both execution paths share one typed frontier kernel.",
        ),
        highlights=(
            "One canonical workflow JSON for visual editors, Coding Agents, Git, and deployment",
            "Typed ports, explicit mappings, and fail-fast validation",
            "Interpret directly or compile to standalone Python with identical semantics",
            "SDK and Claude Code/Codex CLI agent nodes with tools and MCP",
            "Dynamic fan-out, subflows, checkpoints, human signals, and local scheduling",
            "Interactive TUI today; local Workflow JSON Web UI is the next product surface",
        ),
        install="pip install xdog-flow",
    ),
)

PACKAGES_BY_NAME: dict[str, Package] = {p.name: p for p in PACKAGES}

# Layered story shown on the home page: which packages build on which.
LAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Foundation", ("ai", "tui")),
    ("Runtime", ("agent", "flow")),
    ("Products", ("coding", "claw")),
)
