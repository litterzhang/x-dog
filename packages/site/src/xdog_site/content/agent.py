"""Dynamic (Features + Roadmap) content for the ``agent`` package.

agent's static pages (Overview / Design / Reference) are markdown under
``content/pages/agent/``; its Features and Roadmap stay in Python here. Accurate
against packages/agent/src/agent: the Agent wrapper + two-loop core, AgentTool
model, the StreamFn bridge, built-in tools, steering/follow-up queues, and the
xdog-agent CLI.
"""

from __future__ import annotations

from xdog_site.content.docs import Feature, PackageDocs, Phase

_FEATURES = (
    Feature("Tool-calling loop", "Stream → extract tool calls → execute → feed back → repeat until "
            "the model stops.", "Loop"),
    Feature("Parallel tool execution", "Independent tool calls in a turn run concurrently; sequential "
            "mode is available. Parallel is the default.", "Loop"),
    Feature("Steering", "Interrupt the current turn mid-flight and skip its remaining tool calls.",
            "Control"),
    Feature("Follow-up queue", "Inject the next instruction after the turn completes, with ALL / "
            "ONE_AT_A_TIME draining.", "Control"),
    Feature("Cancellation", "abort() / reset_abort() drive an asyncio.Event that unwinds the loop.",
            "Control"),
    Feature("Lifecycle events", "Ten typed events (message update, tool start/end, turn end…) let a "
            "UI observe a run.", "Control"),
    Feature("Hooks", "before_tool_call / after_tool_call, transform_context, and convert_to_llm hook "
            "points around the loop.", "Extensibility"),
    Feature("Argument validation", "Tool arguments are validated against the JSON schema before "
            "execute is called.", "Extensibility"),
    Feature("Incremental progress", "A tool's on_update callback streams progress while it runs.",
            "Extensibility"),
    Feature("Shared tool context", "A tool_ctx dict threads state between tools and back to the "
            "caller (the sink pattern).", "Extensibility"),
    Feature("Built-in: filesystem", "read / write / delete / edit / ls / grep / find as one "
            "multi-action tool.", "Built-in tools"),
    Feature("Built-in: bash", "Run shell commands from the agent.", "Built-in tools"),
    Feature("Built-in: current_time", "Inject the current time.", "Built-in tools"),
    Feature("Built-in: submit_result", "Schema-validated structured output — the agent returns a "
            "typed object, not prose.", "Built-in tools"),
)

_FEATURE_CATEGORIES = ("Loop", "Control", "Extensibility", "Built-in tools")

_ROADMAP = (
    Phase("Shipped", "The agent loop", (
        "Two-loop core: follow-ups outer, tools + steering inner",
        "Parallel and sequential tool execution",
        "Steering, follow-up queues, cooperative cancellation",
        "Ten lifecycle events for UI observation",
    ), done=True),
    Phase("Shipped", "Tools & extensibility", (
        "AgentTool model + declarative ToolDef / @action / Param",
        "Built-ins: filesystem, bash, current_time, submit_result",
        "before/after tool hooks, context transforms, arg validation",
        "Tool registry SPI and shared tool_ctx sink",
    ), done=True),
    Phase("2026", "Multi-agent & delegation", (
        "First-class sub-agents a tool can spawn and await",
        "Structured hand-off between specialised agents",
        "Budgets and depth limits for delegated work",
    )),
    Phase("2026", "Interop & memory", (
        "MCP (Model Context Protocol) tool servers as AgentTools",
        "Pluggable long-term memory hooks around the loop",
        "Replayable transcripts for eval harnesses",
    )),
)

DOCS = PackageDocs(
    name="agent",
    features_intro="What the agent runtime does today. Each capability is a small, testable unit of "
                   "the loop.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    roadmap_intro="Shipped foundations plus where agent is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)
