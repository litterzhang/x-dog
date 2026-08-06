"""Ls tool: list directory contents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xdog.coding.core.tools import AgentTool
from xdog.coding.core.tools.path_utils import validate_path
from xdog.coding.core.tools.truncate import truncate_output


class LsTool(AgentTool):
    """List the contents of a directory."""

    @property
    def name(self) -> str:
        return "ls"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. Shows files and subdirectories "
            "with type indicators (d/ for dirs, -> for symlinks). "
            "The path must be an absolute path."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the directory to list.",
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files (dotfiles). Default: false.",
                },
            },
            "required": ["path"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        dir_path = params.get("path", "")
        show_hidden = bool(params.get("show_hidden", False))

        error = validate_path(dir_path)
        if error:
            return error

        path = Path(dir_path)
        if not path.is_dir():
            return f"Error: not a directory: {dir_path}"

        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            return f"Error listing directory: {exc}"

        lines: list[str] = []
        for entry in entries:
            name = entry.name
            if not show_hidden and name.startswith("."):
                continue

            if entry.is_symlink():
                try:
                    target = entry.resolve()
                    lines.append(f"  {name} -> {target}")
                except OSError:
                    lines.append(f"  {name} -> (broken symlink)")
            elif entry.is_dir():
                lines.append(f"  {name}/")
            else:
                # Show file size
                try:
                    size = entry.stat().st_size
                    lines.append(f"  {name}  ({_human_size(size)})")
                except OSError:
                    lines.append(f"  {name}")

        if not lines:
            return "(empty directory)"

        header = f"Directory: {dir_path}\n"
        return truncate_output(header + "\n".join(lines))


def _human_size(size: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            if unit == "B":
                return f"{size} {unit}"
            return f"{size:.1f} {unit}"
        size //= 1024
    return f"{size:.1f} TB"
