# X-Dog (Python)

> Tools for building AI agents and managing LLM deployments.

## Packages

| Package | CLI Command | Description |
|---------|-------------|-------------|
| **ai** | `xdog-ai` | Unified multi-provider LLM API (Anthropic, OpenAI, Google, Bedrock, Mistral, Copilot) |
| **agent** | `xdog-agent` | Agent runtime with tool calling, steering, and state management |
| **tui** | — | Terminal UI library with differential rendering |
| **coding** | `xdog-coding` | Interactive coding agent CLI with session management and TUI |
| **claw** | `xdog` | AI agent orchestration runtime (NanoClaw/OpenClaw pattern) |

## Setup

```bash
# Requires Python 3.11+
pyenv local 3.13.5

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode (all packages)
pip install -e .

# Install dev dependencies
pip install -e ".[dev]"
```

## Development

```bash
# Run all tests
pytest

# Run tests for a specific package
pytest tests/ai               # LLM API tests
pytest tests/agent             # agent runtime tests
pytest tests/coding            # coding agent CLI tests
pytest tests/claw           # orchestration runtime tests
pytest tests/tui               # terminal UI tests

# Run a single test file
pytest -k test_agent_tools

# Lint
ruff check . --line-length 120 --target-version py311

# Type check
mypy src/ --strict
```

## Architecture

```
                    +-----------+
                    | pods      |  GPU pod management (vLLM)
                    +-----------+
                         |
+--------+    +--------+    +-----------+    +----------------+
| tui    | -> | claw   | -> | agent     | -> |    ai          |
| (TUI)  |    | (orch) |    | (runtime) |    | (LLM gateway)  |
+--------+    +--------+    +-----------+    +----------------+
     |             |                              |
     |       +-----+-----+              +--------+--------+
     |       | mom       |              | Providers:      |
     |       | (Slack    |              | Anthropic       |
     |       |  bot)     |              | OpenAI          |
     |       +-----------+              | Google/Vertex   |
     |                                  | Bedrock         |
     +-- coding                        | Mistral         |
         (xdog-coding CLI)              | Copilot         |
                                        +-----------------+
```

## License

Copyright (c) 2026 HugeMan <942295.xyz>

Licensed under the **GNU Affero General Public License v3.0 or later** — see
[LICENSE](LICENSE). Fork it or offer it as a service, and share your changes.

**What flow compiles for you is yours.** `xdog-flow generate` copies parts of
flow's own runtime into its output, so the AGPL would otherwise follow them into
every compiled workflow. The [flow Generated Output Exception](LICENSE-EXCEPTION.md)
— an Additional Permission under AGPL section 7, modelled on the GCC Runtime
Library Exception — lets you convey generated modules, portable bundles,
scheduling units and workflow definitions under any terms, including proprietary
and commercial ones.
