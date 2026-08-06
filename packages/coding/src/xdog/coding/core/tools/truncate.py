"""Truncate tool: truncate long tool output to fit context limits."""

from __future__ import annotations

from typing import Any

from xdog.coding.core.defaults import MAX_TOOL_OUTPUT_CHARS
from xdog.coding.core.tools import AgentTool


def truncate_output(
    text: str,
    *,
    max_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> str:
    """Truncate *text* to at most *max_chars*, appending a notice if truncated."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    remaining = len(text) - max_chars
    return truncated + f"\n\n... (truncated, {remaining:,} characters omitted)"


class TruncateTool(AgentTool):
    """Explicitly truncate text that is too long for the context window."""

    @property
    def name(self) -> str:
        return "truncate"

    @property
    def description(self) -> str:
        return (
            "Truncate a long piece of text to fit within the context window. "
            "Useful when a previous tool returned very large output."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to truncate.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Maximum character count. Default: {MAX_TOOL_OUTPUT_CHARS}.",
                },
            },
            "required": ["text"],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        text = params.get("text", "")
        max_chars = int(params.get("max_chars", MAX_TOOL_OUTPUT_CHARS))
        return truncate_output(text, max_chars=max_chars)
