from __future__ import annotations

from typing import Any, Awaitable, Callable

from ai.types import TextContent

from agent.core import AgentTool, AgentToolResult


def create_embed_tool_from_fn(embed_fn: Callable[[str], Awaitable[list[float]]]) -> AgentTool:
    """Create an embed tool backed by an injected embed function.

    Parameters
    ----------
    embed_fn:
        ``async (text: str) -> list[float]`` — returns embedding vector.
    """

    async def execute(
        tool_call_id: str,
        args: dict[str, Any],
        cancel: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        text = args.get("text", "")
        if not text:
            return AgentToolResult(content=(TextContent(text="Error: no text provided"),))

        try:
            vector = await embed_fn(text)
            dims = len(vector)
            preview = ", ".join(f"{v:.6f}" for v in vector[:8])
            return AgentToolResult(content=(TextContent(text=f"{dims} dims: [{preview}, ...]"),))
        except Exception as exc:
            return AgentToolResult(content=(TextContent(text=f"Embedding error: {exc}"),))

    return AgentTool(
        name="embed",
        description="Generate text embeddings for semantic similarity and search.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to embed."}},
            "required": ["text"],
        },
        execute=execute,
    )
