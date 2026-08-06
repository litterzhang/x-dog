---
title: Design
---

How ai turns many model backends into one typed interface — the ideas behind the
Provider / Protocol / Vendor split and the immutable type model.

## Three layers: Provider → Protocol → Vendor

The core (`core.py`) splits an LLM integration into three responsibilities so
each can change without disturbing the others. A **Provider** is the user-facing
surface — stream a completion, run a web search, embed text. A **Protocol** is
the wire format that serialises a request and parses the response. A **Vendor**
owns authentication and the model catalog.

Because these are separate, one vendor (Copilot) can speak three different wire
protocols, and application code that holds a Provider never learns which protocol
carried a given call.

## One immutable type model, no vendor SDK leakage

Everything crossing the boundary is a frozen dataclass in `types.py`: `Context`
(messages + system prompt + tools), `StreamOptions` (thinking level, temperature,
max tokens, cancel event, web search), `Model` (routing id, pricing, capability
flags), and the message and content types. No Anthropic/OpenAI SDK object ever
reaches your code.

Immutability means a `Context` is safe to fan out across concurrent calls; you
build a new one to add a message rather than mutating shared state.

## Streaming as a typed event union

A stream is an `EventStream` — an async iterator that also exposes a `.result()`
future for the final assembled message. Each yielded event is a discriminated
union member: `text_delta`, `thinking_delta`, the `tool_call` lifecycle, `usage`,
`status`, `done`, and `error`.

Consumers switch on the event type instead of parsing provider-specific SSE, so
the same loop drives any backend and any wire protocol.

## Routing by "provider/model" string

A `Runtime` (`providers/runtime.py`) aggregates the active providers and resolves
a `"provider/model"` string to the right backend. Swapping models — or, once more
vendors ship, swapping vendors — is a string change, not a code change.

## Prompt caching and cost as first-class data

A system prompt can be split into `SystemPromptBlock`s, each flagged cacheable,
so the static base is cached across turns while per-turn context is not. `Usage`,
`CostBreakdown`, and `ModelCost` travel with every response; Copilot in
particular meters premium requests with a multiplier rather than per-token
dollars, and that is modelled explicitly.
