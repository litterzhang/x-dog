"""Bash tool: execute shell commands."""

from __future__ import annotations

from typing import Any

from coding.core.bash_executor import BashExecutor
from coding.core.defaults import MAX_BASH_TIMEOUT_MS
from coding.core.tools import AgentTool
from coding.core.tools.truncate import truncate_output


class BashTool(AgentTool):
    """Execute a bash command and return its output."""

    def __init__(self, executor: BashExecutor) -> None:
        self._executor = executor

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return (
            "Execute a bash command in the shell. The working directory persists "
            "between calls. Commands time out after 2 minutes by default (max 10 min). "
            "Use for git, npm, docker, builds, and other terminal operations."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": (
                        "Optional timeout in milliseconds (max 600000). "
                        "Defaults to 120000 (2 minutes)."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the command does.",
                },
            },
            "required": ["command"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        command = params.get("command", "")
        if not command:
            return "Error: no command provided."

        timeout_ms = params.get("timeout_ms")
        if timeout_ms is not None:
            timeout_ms = max(1000, min(int(timeout_ms), MAX_BASH_TIMEOUT_MS))

        result = await self._executor.execute_async(command, timeout_ms=timeout_ms)

        if result.timed_out:
            return f"Command timed out.\n{result.stderr}"

        output = result.output.rstrip()
        if result.exit_code != 0:
            header = f"Exit code: {result.exit_code}\n"
            return truncate_output(header + output)

        return truncate_output(output) if output else "(no output)"
