"""Deep-dive content for the ``agent`` package — the tool-calling agent loop.

Accurate against packages/agent/src/agent: the Agent wrapper + two-loop core,
AgentTool model, the StreamFn bridge, built-in tools, steering/follow-up queues,
and the xdog-agent CLI.
"""

from __future__ import annotations

from xdog_site.content.docs import Feature, PackageDocs, Phase, RefBlock, Section

_DESIGN = (
    Section(
        heading="A loop that turns model + tools into an agent",
        body=(
            "At the centre is agent_loop (agent_loop.py): stream the model, extract the tool calls "
            "from the assistant message, execute them, feed the results back, and repeat until the "
            "model stops asking for tools. That single loop is what separates an agent from a bare "
            "completion.",
            "It is a two-loop structure — an outer loop that drains queued follow-ups and an inner "
            "loop that runs tools and honours steering interrupts between turns.",
        ),
    ),
    Section(
        heading="Agent owns immutable state",
        body=(
            "The Agent wrapper (agent.py) holds an AgentState that is only ever replaced (via "
            "dataclasses.replace), never mutated in place. It also owns event subscriptions and the "
            "steering / follow-up queues, so callers observe a running turn without reaching into its "
            "internals.",
        ),
    ),
    Section(
        heading="Tools are plain objects",
        body=(
            "An AgentTool is name + description + JSON-schema params + an async "
            "execute(id, params, cancel, on_update, ctx). Adding a capability is a small, testable "
            "unit — no framework base class to subclass.",
            "A declarative layer (ToolDef / @action / Param) builds multi-action tools where one tool "
            "exposes several verbs, and a registry SPI lets applications discover tools by name.",
        ),
    ),
    Section(
        heading="StreamFn decouples the loop from any provider",
        body=(
            "The loop never imports a model SDK; it depends on a StreamFn Protocol. "
            "stream_fn_from_provider (helpers.py) bridges an ai provider into that Protocol, so the "
            "same agent runs against anything the ai package can reach — or a test double.",
        ),
    ),
    Section(
        heading="Steering, follow-ups, and cancellation",
        body=(
            "Steering interrupts the current turn and skips the remaining tool calls; a follow-up is "
            "injected after the turn completes. Both are queues with ALL or ONE_AT_A_TIME modes. "
            "abort() flips an asyncio.Event that unwinds the loop cooperatively.",
        ),
    ),
)

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

_REFERENCE = (
    RefBlock(
        heading="Top-level API",
        body=("The names exported from agent/__init__.py that applications use directly.",),
        columns=("Name", "Kind", "Purpose"),
        rows=(
            ("Agent", "class", "Stateful wrapper: state, events, steering / follow-up queues."),
            ("agent_loop / run_agent_loop", "function", "The raw tool-calling loop and its runner."),
            ("AgentConfig", "type", "Model, system prompt, execution mode, limits."),
            ("AgentTool", "type", "name + description + JSON-schema params + async execute."),
            ("AgentToolResult", "type", "A tool's return payload."),
            ("StreamFn / EmbedFn / WebSearchFn", "type", "Protocols bridging to the model backend."),
            ("ToolDef / @action / Param", "api", "Declarative multi-action tool framework."),
        ),
    ),
    RefBlock(
        heading="Built-in tools",
        body=("Shipped in agent/tools/. bash, filesystem, current_time, and submit_result "
              "auto-register; web_search and embed are injected from the provider.",),
        columns=("Tool", "Actions / purpose"),
        rows=(
            ("filesystem", "read · write · delete · edit · ls · grep · find"),
            ("bash", "Run a shell command"),
            ("current_time", "Return the current time"),
            ("submit_result", "Schema-validated structured output"),
            ("web_search", "Provider-backed web search (injected)"),
            ("embed", "Provider-backed embedding (injected)"),
        ),
    ),
    RefBlock(
        heading="CLI — xdog-agent",
        body=("Subcommands of the agent console script.",),
        columns=("Command", "Does"),
        rows=(
            ("login", "Authenticate the Copilot backend (GitHub device code)."),
            ("chat [model] [msg]",
             "Interactive or one-shot chat with tools loaded (-s, -t, --max-tokens, --thinking, "
             "--no-tools, --tool-ctx…)."),
        ),
    ),
    RefBlock(
        heading="Chat slash commands",
        body=("Available inside xdog-agent chat.",),
        columns=("Command", "Does"),
        rows=(
            ("/model", "Switch model."),
            ("/thinking", "Set reasoning depth."),
            ("/image", "Attach an image."),
            ("/tools", "List loaded tools."),
            ("/status", "Show session token / cost totals."),
            ("/clear · /verbose · /help · /exit", "Session controls."),
        ),
    ),
)

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
    design_intro="How agent turns a model plus a set of tools into an autonomous loop — the ideas "
                 "behind the two-loop core, immutable state, and the plain-object tool model.",
    design_sections=_DESIGN,
    features_intro="What the agent runtime does today. Each capability is a small, testable unit of "
                   "the loop.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    reference_intro="The top-level API, the built-in tools, and the xdog-agent CLI.",
    reference_blocks=_REFERENCE,
    roadmap_intro="Shipped foundations plus where agent is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)
