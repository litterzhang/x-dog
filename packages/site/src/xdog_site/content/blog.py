"""Seed blog articles for the x-dog site.

Same dict shape as depins' blog (slug, title, description, body, date, tags),
minus i18n. New posts can be appended here.
"""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict


class Article(TypedDict):
    """One blog article."""

    slug: str
    title: str
    description: str
    body: list[str]
    date: datetime
    tags: list[str]


ARTICLES: list[Article] = [
    {
        "slug": "why-a-ports-model-beats-shared-state",
        "title": "Why a Ports Model Beats Shared State for Agent Workflows",
        "description": (
            "How flow moves data between nodes through named ports and explicit edge mappings "
            "instead of a shared global dict — and why that makes pipelines easier to reason about."
        ),
        "body": [
            "Most workflow engines pass data between steps through one shared, flat state dict. "
            "It is convenient at first, but it hides the real data dependencies: any node can read "
            "or clobber any key, and a rename in one place silently breaks a consumer somewhere else.",
            "flow takes the opposite stance. Every node declares typed input and output ports, and "
            "every edge carries an explicit mapping — nodeA.output.x feeds nodeB.input.a. The wiring "
            "is spelled out, not implied by matching key names.",
            "That single decision buys a lot. The graph can be validated before it runs: unknown "
            "ports, two producers feeding one input, or an unfed required input all fail fast at "
            "load time. Port-local interpolation means a node's prompt template only sees its own "
            "inputs, so there is no accidental coupling to unrelated state.",
            "It also makes the workflow legible. A reader (or a generated ASCII diagram) can see "
            "exactly which data each edge carries. When you later compile the workflow to Python, "
            "the ports become ordinary function parameters — no magic global to thread through.",
        ],
        "date": datetime(2026, 5, 12, 10, 0, 0),
        "tags": ["flow", "design", "workflows"],
    },
    {
        "slug": "compiling-agent-workflows-to-python",
        "title": "Compiling Agent Workflows to Self-Contained Python",
        "description": (
            "flow can run a workflow JSON directly or compile it to a standalone Python module. "
            "Here is why codegen matters and what the generated code looks like."
        ),
        "body": [
            "A JSON workflow is great for authoring and editing, but shipping an interpreter into "
            "production is not always what you want. flow's codegen turns a workflow definition into "
            "a single, readable Python module you can vendor, review, and run with no engine.",
            "The generated module keeps node outputs in a nested dict keyed by node id and port, "
            "mirroring the runtime's own shape, so the interpreter and the compiled form agree "
            "line-for-line. Script nodes become plain functions; agent nodes call the ai provider "
            "directly; custom tools are imported and registered under their manifest names.",
            "Because the output is ordinary Python, it passes the same ruff and mypy --strict gate "
            "as hand-written code. That means a workflow is not a black box — it is source you can "
            "diff, test, and step through in a debugger.",
            "The workflow you author, the diagram you review, and the module you deploy are three "
            "views of the same definition. Codegen is what keeps them in sync.",
        ],
        "date": datetime(2026, 5, 20, 12, 30, 0),
        "tags": ["flow", "codegen", "python"],
    },
    {
        "slug": "adding-a-custom-tool-to-a-flow-workflow",
        "title": "Adding a Custom Tool to a flow Agent — From JSON",
        "description": (
            "flow agents can call tools you declare in the workflow JSON via a module:func reference "
            "— loaded at run and generate time, then referenced by name like a built-in."
        ),
        "body": [
            "Agent nodes list tools by name. Built-ins like filesystem and bash resolve out of the "
            "box, but real workflows need domain-specific tools. flow lets you declare them in a "
            "top-level tools manifest that maps a name to a module:func reference — symmetric with "
            "how script nodes reference their run function.",
            "At run and generate time the reference is imported (with the workflow's own directory "
            "on the path, so a workflow can bundle its tool .py files), coerced to an AgentTool — an "
            "instance or a zero-arg factory both work — and registered under the manifest name. That "
            "name is authoritative, so the tool the model sees matches exactly what the node "
            "references.",
            "Validation fails fast: a node that names a tool which is neither a built-in nor a "
            "manifest entry errors at load time, listing the known tools. No silent typos.",
            "The result is that custom capabilities are a small, declarative addition — no forking "
            "the engine, no wiring code — and the interactive builder's Tools page shows each tool's "
            "description, schema, and source alongside the built-ins.",
        ],
        "date": datetime(2026, 6, 3, 9, 15, 0),
        "tags": ["flow", "tools", "agents"],
    },
    {
        "slug": "differential-rendering-terminal-uis",
        "title": "Flicker-Free Terminal UIs with Differential Rendering",
        "description": (
            "How the tui package repaints only what changed — staying in the main terminal buffer, "
            "no alternate screen — to build smooth, scrollback-friendly TUIs."
        ),
        "body": [
            "The classic way to build a full-screen terminal app is to switch to the alternate "
            "screen buffer and repaint everything each frame. It works, but it throws away your "
            "scrollback and can flicker.",
            "The tui package takes a different approach. Components return plain lines of text; the "
            "engine diffs the new frame against the previous one and emits only the escape sequences "
            "needed to update the lines that actually changed. It renders in the main buffer, so your "
            "history stays intact.",
            "On top of that sits a complete input layer: a key parser that understands xterm escape "
            "sequences and the Kitty keyboard protocol (including modifiers and backtab), plus inline "
            "image support through the Kitty and iTerm2 graphics protocols with pure-Python image "
            "sizing — no Pillow dependency.",
            "It is the rendering foundation the coding CLI and the flow workflow builder are both "
            "built on, which is why they feel responsive even while an agent streams output.",
        ],
        "date": datetime(2026, 6, 18, 14, 0, 0),
        "tags": ["tui", "terminal", "rendering"],
    },
]

# Newest first.
ARTICLES.sort(key=lambda a: a["date"], reverse=True)

ARTICLES_BY_SLUG: dict[str, Article] = {a["slug"]: a for a in ARTICLES}
