# Changelog

Notable changes from 1.0.0 onwards. All seven packages share one version number
and are released together.

The format is loosely [Keep a Changelog](https://keepachangelog.com/), and the
project follows [semantic versioning](https://semver.org/).

## [Unreleased]

Nothing yet.

---

## [1.1.0] — 2026-08-07

### Added

- **`inherit` on agent nodes** (`xdog-flow`). An agent node can start from
  another agent node's session — its messages and system prompt — instead of
  cold:

  ```jsonc
  {
    "id": "critique",
    "type": "agent",
    "inherit": { "from": "research" },
    "system_prompt": "You are now a harsh reviewer of your own work.",
    "prompt": "Critique the findings you just produced."
  }
  ```

  The node's own `system_prompt` and `model` override what it inherited. Tools
  are never inherited, so a node's capabilities stay readable from the node
  itself.

  The edge between the two may carry an empty `map`: it establishes order and
  reachability while the session carries the context. That keeps the format's
  central promise — data moves only along things the graph can see — because
  `inherit` is a static reference the validator checks, not an ambient channel.

  A node may inherit from itself, which in a loop is the point: it keeps its own
  context across iterations, so a reviser remembers what it already tried. The
  first pass has no session, which is not an error — the same lenient rule as a
  non-required loop-carried port.

  Strict at load, lenient at run. Because a missing session is tolerated, a typo
  in `from` would otherwise do nothing and never say so; the reference is
  therefore checked before anything runs, and only its absence is forgiven
  later. Ten rules, each with a distinct message. Two of them exist because they
  fail silently otherwise: a `deterministic` source returns memoised ports
  *without running*, so it can never produce a session at all; and a fan-out
  worker runs N times under one node id, so "the" session is ambiguous. CLI
  backends are rejected in both directions — the CLI owns its own session, and
  flow can neither read nor seed it.

  New example: `packages/flow/examples/research_critique.json`.

- **`Agent.dump()` / `Agent.restore()`** (`xdog-agent`). An `Agent` instance is
  a session; these are its two projections, covering the history, the system
  prompt (string or block form), the model, and the value half of
  `StreamOptions`. `StreamOptions.cancel` is excluded — an `asyncio.Event` is a
  live handle, not something that belongs in a file. Message serialization moved
  here from `xdog-coding`, where two other packages had been reimplementing it.

- **`sessions`** in the flow checkpoint schema, so a resumed run does not hand
  an inheriting node an empty context. Optional on read: a checkpoint written
  before this feature still resumes.

### Fixed

- **Restoring an `xdog-claw` session silently lost data.** Its transcript format
  flattened message content to a string, dropping every image, every thinking
  block including its `thinking_signature` — the continuity token for extended
  reasoning — and all but the first part of a tool result. Nothing failed; the
  restored agent was simply having a different conversation from the saved one.

### Changed

- **claw transcript entries changed shape** (`xdog-claw`, internal). An entry is
  now the agent's lossless dict with claw's `timestamp` and `usage` alongside. A
  tool result's role is `"toolResult"`, not `"tool"`, and `content` is a list of
  typed parts rather than a string — read it with `entry_text` /
  `entry_tool_calls`. Old transcripts are not readable; claw has no users, and
  keeping a lossy reader would only have preserved the bug above.

---

## [1.0.0] — 2026-08-06

First stable release. Seven packages under one `xdog` namespace, installable
side by side:

| Package | Import | What it does |
|---|---|---|
| `xdog-flow` | `xdog.flow` | Typed workflow format, validator, interpreter, Python compiler, systemd scheduler |
| `xdog-ai` | `xdog.ai` | Unified LLM provider API — chat, embeddings, web search, Anthropic-compatible proxy |
| `xdog-agent` | `xdog.agent` | Agent runtime: tool calling, structured output, steering, skills |
| `xdog-coding` | `xdog.coding` | Interactive coding-agent CLI with session management |
| `xdog-claw` | `xdog.claw` | Agent orchestration runtime |
| `xdog-tui` | `xdog.tui` | Terminal UI library with differential rendering |
| `xdog-site` | `xdog.site` | The documentation site |

What makes it 1.0 rather than another 0.57: every package passes
`mypy --strict`, and all seven are in the CI gate along with the full test suite
and `ruff`. Reaching that meant fixing real defects rather than adding
annotations — among them a coding CLI that could not process a single message,
a claw package that raised on import, and three packages that did not declare
dependencies they import.

The checks that landed alongside them are the durable part: CI imports every
distribution it publishes, compares each package's declared dependencies against
its actual imports, builds a session and rebuilds its system prompt, and asserts
that every file the shipped skill points at exists. Each of those reproduces a
failure that had already shipped.

Also in 1.0:

- **Stable error codes on `xdog-flow validate --json`** — eighteen of them, with
  a `hint` on about a third. Without a code, a caller reacting differently to
  "that port does not exist" and "those two types are incompatible" has to
  pattern-match English, which makes every reworded message a silent breaking
  change.
- **Skills** in the open [Agent Skills](https://agentskills.io/specification)
  format, discovered from installed packages: `pip install xdog-flow` provides
  `/flow-workflows`, and uninstalling removes it. Two deliberate departures from
  the format's reference clients — a skill can be **unloaded**, because its
  instructions go into the system prompt rather than the transcript; and the
  **model cannot unload one**, since a skill is often a constraint and the
  constrained party should not hold the release.

[Unreleased]: https://github.com/litterzhang/x-dog/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/litterzhang/x-dog/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/litterzhang/x-dog/releases/tag/v1.0.0
