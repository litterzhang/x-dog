# xdog-ai

**Unified LLM provider API.**

A single interface over LLM providers — chat, embeddings, web search, and an
Anthropic-compatible proxy. Ships the `xdog-ai` CLI for logging in, listing
models, and talking to one from a terminal.

```bash
uv run xdog-ai login copilot
uv run xdog-ai chat copilot gpt-5.6-sol "Explain the CAP theorem in three sentences."
```

## Part of xdog

This package is one piece of [xdog](https://github.com/litterzhang/xdog), a
local-first toolkit for building, running and scheduling LLM workflows. The
centrepiece is [`xdog-flow`](https://pypi.org/project/xdog-flow/) — a typed
workflow format and compiler.

Documentation: **https://xdog.942295.xyz**

## Licence

Copyright (c) 2026 HugeMan <942295.xyz>

GNU Affero General Public License v3.0 or later — see
[LICENSE](https://github.com/litterzhang/xdog/blob/main/LICENSE). Output compiled
by `xdog-flow` is exempt; see the
[Generated Output Exception](https://github.com/litterzhang/xdog/blob/main/LICENSE-EXCEPTION.md).
