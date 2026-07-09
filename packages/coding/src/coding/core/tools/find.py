"""Find tool: locate files by glob patterns."""

from __future__ import annotations

from typing import Any

from coding.core.bash_executor import BashExecutor
from coding.core.tools import AgentTool
from coding.core.tools.truncate import truncate_output


class FindTool(AgentTool):
    """Find files matching glob patterns using ``fd`` or ``find``."""

    def __init__(self, executor: BashExecutor) -> None:
        self._executor = executor

    @property
    def name(self) -> str:
        return "find"

    @property
    def description(self) -> str:
        return (
            "Find files matching a glob pattern. Uses fd (if available) or "
            "find as a fallback. Returns matching file paths sorted by "
            "modification time."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g. '**/*.py', 'src/**/*.ts').",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Defaults to cwd.",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Limit output to first N results.",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        pattern = params.get("pattern", "")
        if not pattern:
            return "Error: pattern is required."

        search_path = params.get("path", ".")
        head_limit = params.get("head_limit")

        # Try fd first, fall back to find
        command = self._build_fd_command(pattern, search_path)
        result = await self._executor.execute_async(command, timeout_ms=30_000)

        # If fd is not found, try with find
        if result.exit_code != 0 and "not found" in result.stderr.lower():
            command = self._build_find_command(pattern, search_path)
            result = await self._executor.execute_async(command, timeout_ms=30_000)

        output = result.output.rstrip()
        if not output:
            return "No matching files found."

        # Apply head limit
        if head_limit is not None and int(head_limit) > 0:
            lines = output.splitlines()
            output = "\n".join(lines[: int(head_limit)])

        return truncate_output(output)

    @staticmethod
    def _build_fd_command(pattern: str, path: str) -> str:
        """Build an fd command."""
        quoted_pattern = "'" + pattern.replace("'", "'\\''") + "'"
        quoted_path = "'" + path.replace("'", "'\\''") + "'"
        return f"fd --glob {quoted_pattern} {quoted_path} --type f 2>/dev/null | sort"

    @staticmethod
    def _build_find_command(pattern: str, path: str) -> str:
        """Build a POSIX find command."""
        quoted_path = "'" + path.replace("'", "'\\''") + "'"
        quoted_name = "'" + pattern.replace("'", "'\\''") + "'"
        return f"find {quoted_path} -type f -name {quoted_name} 2>/dev/null | sort"
