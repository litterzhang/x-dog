---
title: Overview
---

*Unified, typed LLM API.*

A single, typed interface over LLM backends, built on a Provider / Protocol /
Vendor split so application code never hard-codes a model SDK. The shipped vendor
today is GitHub Copilot, reached over three wire protocols (openai-completions,
anthropic-messages, openai-responses); the architecture is multi-vendor by
design.

Streaming, tool calls, web search, and token accounting are normalised behind
one immutable Context / StreamOptions model.

## Highlights

- One `provider(id)` call resolves the backend; swap models without touching app code
- Streaming event union, tool calls, and web search behind a typed interface
- Copilot auth via GitHub device code; model catalog sync with offline fallback
- Pure-Python message/type model — no vendor SDK leaks into your code

## Try it

```bash
uv run xdog-ai --help
```
