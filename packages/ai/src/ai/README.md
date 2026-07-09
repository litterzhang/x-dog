# ai

Unified multi-provider LLM API for Python. Stream chat completions, generate embeddings, and perform web searches through a single interface.

## Architecture

Four concepts with clear boundaries:

```
Provider (user-facing, thin)
    |
    +-- Vendor (internal: auth, model sync)
    |       +-- resolve_auth(model) -> AuthResult
    |       +-- sync_models() -> tuple[Model, ...]
    |
    +-- Protocol (internal: wire format)
    |       +-- openai-completions    /v1/chat/completions
    |       +-- anthropic-messages    /v1/messages
    |       +-- openai-responses      /v1/responses
    |
    v
EventStream[AssistantMessage]
```

- **Provider** -- user-facing: `stream`, `complete`, `embed`, `web_search`, `login`, `models`
- **Vendor** -- internal: authentication (returns `AuthResult`), model sync (returns `Model` objects)
- **Protocol** -- internal: wire-format streaming, accepts `StreamOptions + AuthResult`
- **AuthResult** -- frozen dataclass with `api_key`, `headers`, `base_url` (Model is never mutated for auth)

## Installation

```bash
pip install -e .
```

Registers the `pi-ai` CLI command automatically.

## Quick Start

### Python API

```python
import ai

# Get a provider
copilot = ai.provider("copilot")

# Stream chat
ctx = ai.Context(messages=(ai.UserMessage(content="Hello!"),))
async for event in copilot.stream("claude-sonnet-4.5", ctx):
    if event.type == "text_delta":
        print(event.delta, end="")

# Complete (non-streaming)
msg = await copilot.complete("gpt-4o", ctx)
print(msg.content[0].text)

# With reasoning
opts = ai.StreamOptions(thinking="high")
async for event in copilot.stream("claude-sonnet-4.6", ctx, opts):
    if event.type == "thinking_delta":
        print(f"[thinking] {event.delta}")
    elif event.type == "text_delta":
        print(event.delta, end="")

# Embeddings
result = await copilot.embed("text-embedding-3-small", "Hello world")
print(f"{len(result.data[0].embedding)} dimensions")

# Web search
result = await copilot.web_search("goldeneye", "latest Python release")
print(result.content[0].text)
```

### Runtime (multi-provider)

```python
import ai

runtime = ai.load()  # discovers active providers from auth.json
runtime.stream("copilot/claude-sonnet-4.5", ctx)
```

### Vision

```python
import ai
import base64

image_data = base64.b64encode(open("photo.jpg", "rb").read()).decode()
msg = ai.UserMessage(content=(
    ai.TextContent(text="What's in this image?"),
    ai.ImageContent(data=image_data, mime_type="image/jpeg"),
))
ctx = ai.Context(messages=(msg,))

async for event in ai.provider("copilot").stream("gpt-4o", ctx):
    if event.type == "text_delta":
        print(event.delta, end="")
```

### Tool Calling

```python
import ai

weather_tool = ai.Tool(
    name="get_weather",
    description="Get the current weather for a location",
    parameters={
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    },
)
ctx = ai.Context(
    messages=(ai.UserMessage(content="What's the weather in Tokyo?"),),
    tools=(weather_tool,),
)

async for event in ai.provider("copilot").stream("gpt-4o", ctx):
    if event.type == "tool_call_done":
        print(f"Tool: {event.name}({event.arguments})")
```

## CLI

```bash
# Login (GitHub Copilot OAuth device flow)
pi-ai login copilot

# List active providers
pi-ai providers

# List models (from cache)
pi-ai models copilot

# Sync models from provider API
pi-ai models copilot --sync

# Chat (one-shot)
pi-ai chat copilot claude-sonnet-4.5 "Explain quicksort"

# Chat (interactive)
pi-ai chat copilot claude-sonnet-4.5

# Chat with options
pi-ai chat copilot claude-sonnet-4.6 "Prove sqrt(2) is irrational" --thinking high --verbose
pi-ai chat copilot gpt-4o "Describe this" -i photo.jpg
echo "Summarize this" | pi-ai chat copilot gpt-4o

# Embeddings
pi-ai embed copilot text-embedding-3-small "Hello world"
pi-ai embed copilot text-embedding-3-small "Hello" -d 256 --json
echo "text" | pi-ai embed copilot text-embedding-3-small

# Web search
pi-ai search copilot goldeneye "latest Python release"
```

## Module Reference

```
src/ai/
  __init__.py              Public API: provider(), load(), login() + type re-exports
  api.py                   Public API functions
  core.py                  ABCs: BaseProvider, BaseProtocol, BaseVendor, AuthResult
  types.py                 Frozen dataclasses (Model, Context, Message, StreamOptions, events)
  paths.py                 XDG-compliant storage paths
  cli.py                   pi-ai CLI entry point
  providers/
    __init__.py            Provider factory
    copilot.py             CopilotProvider (thin, dispatches to protocols)
    runtime.py             Runtime (aggregates multiple providers)
    testing.py             TestProvider + TestProtocol for unit tests
  protocols/
    openai_completions.py  OpenAI Chat Completions (stream + embed)
    anthropic_messages.py  Anthropic Messages API (stream)
    openai_responses.py    OpenAI Responses API (stream + web search)
    _message_builder.py    Shared mutable message builder for streaming
    _transform_messages.py Message format transformation for OpenAI
  vendors/
    copilot/
      __init__.py          CopilotVendor (auth + token management)
      _model_sync.py       Model sync with API fallback models
  utils/
    event_stream.py        Async EventStream iterator with result
    cost.py                Cost calculation (premium multiplier or per-token)
    json_parse.py          Streaming JSON parser for partial tool args
    overflow.py            Context overflow detection
    hash.py                SHA-256 hashing utilities
    sanitize_unicode.py    Unicode sanitization
    validation.py          Tool argument validation
    auth.py                Reusable OAuth device code flow
```

## Key Types

All core types are **frozen dataclasses**. State updates use `dataclasses.replace()`.

| Type | Purpose |
|------|---------|
| `Model` | Full model spec: ID, provider, protocol, limits, cost, capabilities |
| `ModelCost` | Pricing: premium request multiplier (Copilot) or per-token rates |
| `Context` | Conversation state: messages, system prompt, tools |
| `UserMessage` | User turn (text, images, or mixed content) |
| `AssistantMessage` | Assistant turn (text, thinking, tool calls) |
| `ToolResultMessage` | Tool execution result |
| `StreamOptions` | User-facing options: thinking, temperature, max_tokens, cancel, web_search |
| `AuthResult` | Resolved credentials: api_key, headers, base_url |
| `Usage` | Token counts and cost breakdown |
| `EventStream` | Async iterator over streaming events with final result |
| `EmbeddingRequest` | Input text(s) with optional dimensions |
| `EmbeddingResponse` | Embedding vectors with usage |
| `BaseProvider` | ABC for provider implementations |
| `BaseProtocol` | ABC for wire-format protocols |
| `BaseVendor` | ABC for vendor auth + model sync |

## Streaming Events

Events are discriminated by `event.type`:

| Event | Description |
|-------|-------------|
| `start` | Stream begins |
| `text_delta` | Incremental text token |
| `text_start` / `text_done` | Text block boundaries |
| `thinking_delta` | Reasoning token (when thinking enabled) |
| `thinking_start` / `thinking_done` | Thinking block boundaries |
| `tool_call_start` / `tool_call_delta` / `tool_call_done` | Tool invocation lifecycle |
| `usage` | Token usage update |
| `status` | Status update (e.g. web search progress) |
| `done` | Stream complete, final `AssistantMessage` available |
| `error` | Error during streaming |

## Storage

XDG-compliant paths under `~/.local/x-dog/`:

| File | Purpose |
|------|---------|
| `auth.json` | OAuth tokens (keyed by provider ID) |
| `models_cache.json` | Synced model catalog |

## Testing

```bash
pytest tests/ai/ -q          # 39 tests, 0 warnings
```

## Model Limits

Each `Model` carries three token limits from the API:

| Field | Meaning |
|-------|---------|
| `context_window` | Total token budget (input + output) |
| `max_prompt_tokens` | Maximum input tokens (the real usable limit) |
| `max_tokens` | Maximum output tokens |

## Cost

`Model.cost` holds pricing via `ModelCost`:

- **Copilot** — `cost.input` = premium request multiplier (0 = free, 0.33 = lightweight, 1 = standard, 3 = premium)
- **Per-token providers** — `cost.input`/`output`/`cache_read`/`cache_write` = per-million-token dollar rates

All protocols call `usage_with_cost(model, usage)` at stream end, populating `Usage.cost.total` on every `AssistantMessage`.
