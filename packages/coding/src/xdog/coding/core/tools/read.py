"""Read tool: read file contents with line numbers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xdog.coding.core.defaults import DEFAULT_READ_LIMIT, MAX_LINE_LENGTH
from xdog.coding.core.tools import AgentTool
from xdog.coding.core.tools.path_utils import validate_path


class ReadTool(AgentTool):
    """Read a file and return its contents with line numbers."""

    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return (
            "Read a file from the filesystem. Returns contents with line numbers "
            f"(cat -n style). By default reads up to {DEFAULT_READ_LIMIT} lines. "
            "Lines longer than 2000 chars are truncated. "
            "The file_path must be an absolute path."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-based). Default: 1.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum number of lines to read. Default: {DEFAULT_READ_LIMIT}.",
                },
            },
            "required": ["file_path"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        file_path = params.get("file_path", "")
        offset = max(1, int(params.get("offset", 1)))
        limit = int(params.get("limit", DEFAULT_READ_LIMIT))

        # Validate path
        error = validate_path(file_path)
        if error:
            return error

        path = Path(file_path)
        if not path.is_file():
            return f"Error: file not found: {file_path}"

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Error reading file: {exc}"

        lines = text.splitlines()
        total_lines = len(lines)

        # Apply offset and limit (offset is 1-based)
        start_idx = offset - 1
        end_idx = start_idx + limit
        selected = lines[start_idx:end_idx]

        if not selected:
            if total_lines == 0:
                return "(empty file)"
            return f"Error: offset {offset} is beyond end of file ({total_lines} lines)."

        # Format with line numbers, truncating long lines
        output_lines: list[str] = []
        for i, line in enumerate(selected, start=offset):
            if len(line) > MAX_LINE_LENGTH:
                line = line[:MAX_LINE_LENGTH] + "..."
            # Right-align line number in a field wide enough for the file
            width = len(str(offset + len(selected) - 1))
            output_lines.append(f"{i:>{width + 1}}\t{line}")

        result = "\n".join(output_lines)

        # Append a note if we truncated
        if end_idx < total_lines:
            result += f"\n\n... ({total_lines - end_idx} more lines)"

        return result
