"""Copilot provider — thin user-facing layer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING

from xdog.ai.core import AuthResult, BaseProvider
from xdog.ai.types import (
    Context,
    EmbeddingRequest,
    StreamOptions,
    UserMessage,
)

if TYPE_CHECKING:
    from xdog.ai.types import (
        AssistantMessage,
        AssistantMessageEvent,
        EmbeddingResponse,
        Model,
    )
    from xdog.ai.utils.event_stream import EventStream


class CopilotProvider(BaseProvider):
    """GitHub Copilot provider."""

    def __init__(self) -> None:
        self._vendor = None
        self._model_cache: dict[str, Model] = {}
        self._protocols: dict[str, object] = {}

    @property
    def id(self) -> str:
        return "copilot"

    @property
    def name(self) -> str:
        return "GitHub Copilot"

    # -- Internals ------------------------------------------------------------

    def _get_vendor(self):
        if self._vendor is None:
            from xdog.ai.vendors.copilot import CopilotVendor
            self._vendor = CopilotVendor()
        return self._vendor

    def _get_protocol(self, protocol_id: str):
        if protocol_id not in self._protocols:
            if protocol_id == "openai-completions":
                from xdog.ai.protocols.openai_completions import OpenAICompletionsProtocol
                self._protocols[protocol_id] = OpenAICompletionsProtocol()
            elif protocol_id == "anthropic-messages":
                from xdog.ai.protocols.anthropic_messages import AnthropicMessagesProtocol
                self._protocols[protocol_id] = AnthropicMessagesProtocol()
            elif protocol_id == "openai-responses":
                from xdog.ai.protocols.openai_responses import OpenAIResponsesProtocol
                self._protocols[protocol_id] = OpenAIResponsesProtocol()
            else:
                raise ValueError(f"Unknown protocol: {protocol_id!r}")
        return self._protocols[protocol_id]

    def _resolve(self, name: str) -> Model:
        full = name if "/" in name else f"copilot/{name}"
        m = self._model_cache.get(full)
        if m is not None:
            return m
        from xdog.ai.vendors.copilot._model_sync import get_synced_model
        m = get_synced_model(full)
        if m is not None:
            self._model_cache[full] = m
            return m
        raise ValueError(f"Unknown model: {name!r}")

    def _wire_model(self, model: Model, auth: AuthResult) -> Model:
        """Prepare model for the wire: strip prefix, apply base_url."""
        return replace(
            model,
            id=model.id.removeprefix("copilot/"),
            base_url=auth.base_url or model.base_url,
        )

    # -- Models ---------------------------------------------------------------

    def models(self) -> tuple[Model, ...]:
        from xdog.ai.vendors.copilot._model_sync import list_models
        return list_models()

    def model(self, name: str) -> Model | None:
        try:
            return self._resolve(name)
        except ValueError:
            return None

    # -- Stream / Complete ----------------------------------------------------

    def stream(self, model_name: str, context: Context, options: StreamOptions | None = None, cancel: asyncio.Event | None = None) -> EventStream[AssistantMessage]:
        from xdog.ai.utils.event_stream import EventStream

        resolved = self._resolve(model_name)
        opts = options or StreamOptions()
        result_future: asyncio.Future[AssistantMessage] = asyncio.get_event_loop().create_future()

        async def _generate() -> AsyncIterator[AssistantMessageEvent]:
            auth = await self._get_vendor().resolve_auth(resolved, context)
            protocol = self._get_protocol(resolved.preferred_protocol or resolved.api)
            wire = self._wire_model(resolved, auth)
            inner = protocol.stream(wire, context, opts, auth)

            async for event in inner:
                yield event

            if hasattr(inner, "result"):
                result_future.set_result(await inner.result())

        return EventStream.from_async_generator(_generate(), result_future)

    async def complete(self, model_name: str, context: Context, options: StreamOptions | None = None, cancel: asyncio.Event | None = None) -> AssistantMessage:
        return await self.stream(model_name, context, options, cancel).result()

    # -- Embed ----------------------------------------------------------------

    async def embed(self, model_name: str, input: str | tuple[str, ...]) -> EmbeddingResponse:
        resolved = self._resolve(model_name)
        request = input if isinstance(input, EmbeddingRequest) else EmbeddingRequest(input=input)

        auth = await self._get_vendor().resolve_auth(resolved)
        protocol = self._get_protocol(resolved.preferred_protocol or resolved.api)
        wire = self._wire_model(resolved, auth)
        return await protocol.embed(wire, request, auth)

    # -- Web search -----------------------------------------------------------

    async def web_search(self, model_name: str, query: str) -> AssistantMessage:
        context = Context(
            system_prompt="You are a web search assistant. Search the web and return a concise, factual summary with source URLs.",
            messages=(UserMessage(content=query),),
        )
        return await self.stream(model_name, context, StreamOptions(web_search=True)).result()

    # -- Auth & sync ----------------------------------------------------------

    async def login(self) -> str:
        return await self._get_vendor().login()

    async def sync_models(self, *, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]:
        models = await self._get_vendor().sync_models(ttl, force)
        for m in models:
            self._model_cache[m.id] = m
        return models

    def __repr__(self) -> str:
        return "CopilotProvider()"
