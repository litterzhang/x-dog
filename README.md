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

MIT
