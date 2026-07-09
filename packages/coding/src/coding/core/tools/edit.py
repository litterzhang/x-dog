"""Edit tool: find-and-replace editing with exact string matching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from coding.core.tools import AgentTool
from coding.core.tools.path_utils import validate_path


class EditTool(AgentTool):
    """Perform exact string replacement in a file."""

    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return (
            "Perform exact string replacements in a file. "
            "The old_string must match exactly (including whitespace and indentation). "
            "The edit will fail if old_string is not unique in the file unless "
            "replace_all is set to true. The file_path must be an absolute path."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find and replace.",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "If true, replace all occurrences. Default: false.",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        file_path = params.get("file_path", "")
        old_string = params.get("old_string", "")
        new_string = params.get("new_string", "")
        replace_all = bool(params.get("replace_all", False))

        if not old_string:
            return "Error: old_string must not be empty."

        if old_string == new_string:
            return "Error: old_string and new_string must be different."

        # Validate path
        error = validate_path(file_path)
        if error:
            return error

        path = Path(file_path)
        if not path.is_file():
            return f"Error: file not found: {file_path}"

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return f"Error reading file: {exc}"

        occurrences = content.count(old_string)
        if occurrences == 0:
            return (
                "Error: old_string not found in file. Make sure you match the exact "
                "text including whitespace and indentation."
            )

        if occurrences > 1 and not replace_all:
            return (
                f"Error: old_string found {occurrences} times in file. "
                "Either provide more surrounding context to make it unique "
                "or set replace_all to true."
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            return f"Error writing file: {exc}"

        count_msg = f"{occurrences} occurrence(s)" if replace_all else "1 occurrence"
        return f"Successfully replaced {count_msg} in {file_path}"
