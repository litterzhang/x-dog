"""Deep-dive content for the ``ai`` package — a unified, typed LLM API.

Accurate against packages/ai/src/ai: the Provider/Protocol/Vendor split, the
frozen-dataclass type model, the streaming event union, model-catalog sync, and
the ``xdog-ai`` CLI. Today the only shipped vendor is GitHub Copilot; the roadmap
is explicit about what is shipped vs. planned.
"""

from __future__ import annotations

from xdog_site.content.docs import Feature, PackageDocs, Phase, RefBlock, Section

_DESIGN = (
    Section(
        heading="Three layers: Provider → Protocol → Vendor",
        body=(
            "The core (core.py) splits an LLM integration into three responsibilities so each can "
            "change without disturbing the others. A Provider is the user-facing surface — stream a "
            "completion, run a web search, embed text. A Protocol is the wire format that serialises "
            "a request and parses the response. A Vendor owns authentication and the model catalog.",
            "Because these are separate, one vendor (Copilot) can speak three different wire "
            "protocols, and application code that holds a Provider never learns which protocol carried "
            "a given call.",
        ),
    ),
    Section(
        heading="One immutable type model, no vendor SDK leakage",
        body=(
            "Everything crossing the boundary is a frozen dataclass in types.py: Context (messages + "
            "system prompt + tools), StreamOptions (thinking level, temperature, max tokens, cancel "
            "event, web search), Model (routing id, pricing, capability flags), and the message and "
            "content types. No Anthropic/OpenAI SDK object ever reaches your code.",
            "Immutability means a Context is safe to fan out across concurrent calls; you build a new "
            "one to add a message rather than mutating shared state.",
        ),
    ),
    Section(
        heading="Streaming as a typed event union",
        body=(
            "A stream is an EventStream — an async iterator that also exposes a .result() future for "
            "the final assembled message. Each yielded event is a discriminated union member: "
            "text_delta, thinking_delta, the tool_call lifecycle, usage, status, done, and error.",
            "Consumers switch on the event type instead of parsing provider-specific SSE, so the same "
            "loop drives any backend and any wire protocol.",
        ),
    ),
    Section(
        heading="Routing by \"provider/model\" string",
        body=(
            "A Runtime (providers/runtime.py) aggregates the active providers and resolves a "
            "\"provider/model\" string to the right backend. Swapping models — or, once more vendors "
            "ship, swapping vendors — is a string change, not a code change.",
        ),
    ),
    Section(
        heading="Prompt caching and cost as first-class data",
        body=(
            "A system prompt can be split into SystemPromptBlocks, each flagged cacheable, so the "
            "static base is cached across turns while per-turn context is not. Usage, CostBreakdown, "
            "and ModelCost travel with every response; Copilot in particular meters premium requests "
            "with a multiplier rather than per-token dollars, and that is modelled explicitly.",
        ),
    ),
)

_FEATURES = (
    Feature("Streaming completions", "Async EventStream with a typed event union and a .result() "
            "future for the final message.", "Model calls"),
    Feature("Tool / function calling", "Tool definitions plus per-model supports_tool_calls and "
            "supports_parallel_tool_calls capability flags.", "Model calls"),
    Feature("Web search", "A built-in provider.web_search() and a StreamOptions.web_search flag that "
            "enables in-turn browsing where the model supports it.", "Model calls"),
    Feature("Thinking levels", "StreamOptions carries a ThinkingLevel so reasoning depth is a "
            "first-class request parameter.", "Model calls"),
    Feature("Cancellation", "A StreamOptions.cancel asyncio.Event aborts an in-flight stream "
            "cooperatively.", "Model calls"),
    Feature("Token & cost accounting", "Usage, CostBreakdown, and ModelCost accompany every response; "
            "Copilot premium-request multipliers are modelled.", "Accounting"),
    Feature("Model catalog sync", "sync_models fetches the live /models list, caches it (24h TTL) with "
            "a bundled fallback set so the picker still works offline.", "Accounting"),
    Feature("Embeddings", "provider.embed() with EmbeddingRequest / EmbeddingResponse types for "
            "vector workloads.", "Model calls"),
    Feature("Prompt caching", "SystemPromptBlock(cache=True) marks the static prompt base cacheable "
            "across turns.", "Accounting"),
    Feature("GitHub OAuth device flow", "Copilot auth uses the GitHub device-code flow, exchanging for "
            "a Copilot JWT — no API key to paste.", "Providers"),
    Feature("Three wire protocols", "Copilot models are reached over openai-completions, "
            "anthropic-messages, or openai-responses, chosen per model.", "Providers"),
    Feature("Anthropic-compatible proxy", "proxy.py serves /v1/messages so Anthropic-SDK clients can "
            "target the same backend unchanged.", "Interop"),
)

_FEATURE_CATEGORIES = ("Model calls", "Accounting", "Providers", "Interop")

_REFERENCE = (
    RefBlock(
        heading="Top-level API",
        body=("The functions and types most application code touches, from ai/__init__.py.",),
        columns=("Name", "Kind", "Purpose"),
        rows=(
            ("provider(id)", "function", "Return a Provider by id (today: \"copilot\")."),
            ("load()", "function", "Build a Runtime over all active, authenticated providers."),
            ("login()", "function", "Run a vendor's auth flow (Copilot: GitHub device code)."),
            ("Context", "type", "Immutable messages + system prompt + tools."),
            ("StreamOptions", "type", "thinking / temperature / max_tokens / cancel / web_search."),
            ("Model", "type", "Routing id, pricing, capability flags, protocol preference."),
            ("EventStream", "type", "Async iterator over stream events + .result() future."),
        ),
    ),
    RefBlock(
        heading="Stream event union",
        body=("Each event yielded by a stream is one of these discriminated members (types.py).",),
        columns=("Event", "Carries"),
        rows=(
            ("text_delta", "An incremental chunk of assistant text."),
            ("thinking_delta", "An incremental chunk of reasoning text."),
            ("tool_call_*", "The tool-call lifecycle (start / delta / end)."),
            ("usage", "Token counts for the turn."),
            ("status", "Provider-side status transitions."),
            ("done", "Terminal event; the final message is on .result()."),
            ("error", "A provider or transport error."),
        ),
    ),
    RefBlock(
        heading="Providers today",
        body=("The shipped vendor set. The architecture is multi-vendor; the catalog is currently one "
              "vendor reached over three protocols.",),
        columns=("Provider", "Auth", "Protocols"),
        rows=(
            ("copilot", "GitHub OAuth device code → Copilot JWT",
             "openai-completions · anthropic-messages · openai-responses"),
        ),
    ),
    RefBlock(
        heading="CLI — xdog-ai",
        body=("Subcommands of the ai console script.",),
        columns=("Command", "Does"),
        rows=(
            ("login [provider]", "Authenticate a provider (default copilot)."),
            ("providers", "List active, authenticated providers."),
            ("models <provider> [--sync]", "List a provider's models; --sync refreshes the catalog."),
            ("chat <provider> <model> [msg]",
             "One-shot or interactive chat (-s, -t, --max-tokens, --thinking, -i, --web-search…)."),
            ("embed", "Embed text to a vector."),
            ("search", "Run a web search through the provider."),
            ("proxy [--host --port --api-key]", "Serve the Anthropic-compatible /v1/messages proxy."),
        ),
    ),
)

_ROADMAP = (
    Phase("Shipped", "Typed multi-provider core", (
        "Provider / Protocol / Vendor three-layer split",
        "Frozen-dataclass Context / StreamOptions / Model type model",
        "Streaming event union with .result() future",
        "Tool calls, web search, thinking levels, cancellation",
    ), done=True),
    Phase("Shipped", "Copilot vendor + accounting", (
        "GitHub device-code auth → Copilot JWT",
        "Three wire protocols (openai-completions / anthropic-messages / openai-responses)",
        "Model catalog sync with 24h cache + offline fallback",
        "Usage / cost accounting with premium-request multipliers",
        "Anthropic-compatible /v1/messages proxy",
    ), done=True),
    Phase("2026", "Beyond one vendor", (
        "Additional first-party vendors behind the same Provider surface",
        "Provider-agnostic model-capability discovery",
        "Cross-provider routing / fallback policies",
    )),
    Phase("2026", "Richer accounting & caching", (
        "Unified per-run cost budgets surfaced to callers",
        "Automatic prompt-cache block placement",
        "Structured-output / JSON-schema responses across protocols",
    )),
)

DOCS = PackageDocs(
    name="ai",
    design_intro="How ai turns many model backends into one typed interface — the ideas behind the "
                 "Provider / Protocol / Vendor split and the immutable type model.",
    design_sections=_DESIGN,
    features_intro="What ai does today. Every capability below is backed by the shipped Copilot "
                   "provider and the typed core.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    reference_intro="The top-level API, the streaming event union, the shipped providers, and the "
                    "xdog-ai CLI.",
    reference_blocks=_REFERENCE,
    roadmap_intro="Shipped foundations plus where ai is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)
