"""Write tool: write file contents to disk."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xdog.coding.core.tools import AgentTool
from xdog.coding.core.tools.path_utils import validate_path


class WriteTool(AgentTool):
    """Write content to a file, creating parent directories as needed."""

    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Creates the file if it does not exist. "
            "Creates parent directories as needed. Overwrites existing content. "
            "The file_path must be an absolute path."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file.",
                },
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        file_path = params.get("file_path", "")
        content = params.get("content", "")

        # Validate path
        error = validate_path(file_path)
        if error:
            return error

        path = Path(file_path)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"Error writing file: {exc}"

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"File written successfully: {file_path} ({line_count} lines)"
