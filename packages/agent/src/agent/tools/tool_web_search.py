from __future__ import annotations

from typing import Any, Awaitable, Callable

from ai.types import TextContent

from agent.core import AgentTool, AgentToolResult


def create_web_search_tool_from_fn(search_fn: Callable[[str], Awaitable[str]]) -> AgentTool:
    """Create a web_search tool backed by an injected search function.

    Parameters
    ----------
    search_fn:
        ``async (query: str) -> str`` — returns search result text.
    """

    async def execute(
        tool_call_id: str,
        args: dict[str, Any],
        cancel: Any = None,
        on_update: Any = None,
        **kwargs: Any,
    ) -> AgentToolResult:
        query = args.get("query", "")
        if not query:
            return AgentToolResult(content=(TextContent(text="Error: no query provided"),))

        try:
            text = await search_fn(query)
            return AgentToolResult(content=(TextContent(text=text or "(no results)"),))
        except Exception as exc:
            return AgentToolResult(content=(TextContent(text=f"Web search error: {exc}"),))

    return AgentTool(
        name="web_search",
        description="Search the web for current information.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query."}},
            "required": ["query"],
        },
        execute=execute,
    )
