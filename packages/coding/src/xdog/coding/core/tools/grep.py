"""Grep tool: search file contents using ripgrep-compatible patterns."""

from __future__ import annotations

from typing import Any

from xdog.coding.core.bash_executor import BashExecutor
from xdog.coding.core.tools import AgentTool
from xdog.coding.core.tools.truncate import truncate_output


class GrepTool(AgentTool):
    """Search file contents using ripgrep (rg)."""

    def __init__(self, executor: BashExecutor) -> None:
        self._executor = executor

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents using ripgrep patterns. "
            "Supports regex, file type filtering, context lines, and "
            "multiple output modes (content, files_with_matches, count)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in. Defaults to cwd.",
                },
                "glob": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g. '*.py').",
                },
                "type": {
                    "type": "string",
                    "description": "File type to search (e.g. 'py', 'js', 'rust').",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": "Output mode. Default: files_with_matches.",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search. Default: false.",
                },
                "context": {
                    "type": "integer",
                    "description": "Lines of context before and after matches.",
                },
                "before_context": {
                    "type": "integer",
                    "description": "Lines of context before each match.",
                },
                "after_context": {
                    "type": "integer",
                    "description": "Lines of context after each match.",
                },
                "multiline": {
                    "type": "boolean",
                    "description": "Enable multiline matching. Default: false.",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Limit output to first N entries.",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        pattern = params.get("pattern", "")
        if not pattern:
            return "Error: pattern is required."

        # Build rg command
        cmd_parts = ["rg", "--no-heading"]
        output_mode = params.get("output_mode", "files_with_matches")

        if output_mode == "files_with_matches":
            cmd_parts.append("-l")
        elif output_mode == "count":
            cmd_parts.append("-c")
        else:
            cmd_parts.append("-n")

        if params.get("case_insensitive"):
            cmd_parts.append("-i")

        if params.get("multiline"):
            cmd_parts.extend(["-U", "--multiline-dotall"])

        context = params.get("context")
        if context is not None:
            cmd_parts.extend(["-C", str(int(context))])
        else:
            before = params.get("before_context")
            after = params.get("after_context")
            if before is not None:
                cmd_parts.extend(["-B", str(int(before))])
            if after is not None:
                cmd_parts.extend(["-A", str(int(after))])

        glob_pattern = params.get("glob")
        if glob_pattern:
            cmd_parts.extend(["--glob", _shell_quote(glob_pattern)])

        file_type = params.get("type")
        if file_type:
            cmd_parts.extend(["--type", file_type])

        cmd_parts.append("--")
        cmd_parts.append(_shell_quote(pattern))

        search_path = params.get("path", ".")
        cmd_parts.append(_shell_quote(search_path))

        command = " ".join(cmd_parts)

        head_limit = params.get("head_limit")
        if head_limit is not None and int(head_limit) > 0:
            command += f" | head -n {int(head_limit)}"

        result = await self._executor.execute_async(command, timeout_ms=30_000)

        output = result.output.rstrip()
        if not output:
            return "No matches found."

        return truncate_output(output)


def _shell_quote(s: str) -> str:
    """Wrap a string in single quotes, escaping internal single quotes."""
    return "'" + s.replace("'", "'\\''") + "'"
