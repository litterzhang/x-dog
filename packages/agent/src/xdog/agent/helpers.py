"""Helpers for building StreamFn, WebSearchFn, EmbedFn from ai providers.

Usage::

    import xdog.ai as ai
    from xdog.agent.helpers import stream_fn_from_provider, web_search_fn_from_provider

    provider = ai.provider("copilot")
    agent = Agent(
        stream_fn_from_provider(provider),
        config=AgentConfig(model="claude-sonnet-4.5"),
        web_search_fn=web_search_fn_from_provider(provider, "claude-sonnet-4.5"),
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xdog.agent.core import EmbedFn
from xdog.ai.core import BaseProvider

if TYPE_CHECKING:
    from xdog.agent.core import StreamFn, WebSearchFn
    from xdog.ai.types import AssistantMessage, Context, StreamOptions
    from xdog.ai.utils.event_stream import EventStream


def stream_fn_from_provider(provider: BaseProvider) -> StreamFn:
    """Build a :class:`~agent.core.StreamFn` from an ai Provider."""

    def _stream(
        model: str,
        context: Context,
        options: StreamOptions,
    ) -> EventStream[AssistantMessage]:
        return provider.stream(model, context, options)

    return _stream


def web_search_fn_from_provider(provider: BaseProvider, model: str) -> WebSearchFn:
    """Build a :class:`~agent.core.WebSearchFn` from an ai Provider.

    The returned function calls ``provider.web_search(model, query)``
    and extracts the text content from the response.
    """

    async def _search(query: str) -> str:
        result = await provider.web_search(model, query)
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else "(no results)"

    return _search


def embed_fn_from_provider(provider: BaseProvider, model: str) -> EmbedFn:
    """Build a :class:`~agent.core.EmbedFn` from an ai Provider.

    The returned function calls ``provider.embed(model, text)``
    and returns the first embedding vector.
    """

    async def _embed(text: str) -> list[float]:
        result = await provider.embed(model, text)
        if result.data:
            return list(result.data[0].embedding)
        return []

    return _embed
