"""Test utilities for the ai package."""

from __future__ import annotations

from typing import Callable

from ai.core import AuthResult, BaseProtocol, BaseProvider
from ai.types import (
    AssistantMessage,
    Context,
    EmbeddingResponse,
    Model,
    ModelCost,
    StreamOptions,
)
from ai.utils.event_stream import EventStream

_TEST_API = "test"
_TEST_PROVIDER = "test"


class _TestProtocol(BaseProtocol):
    """Test protocol backed by an injected stream function.

    The injected ``stream_fn(model, context, options)`` receives
    :class:`StreamOptions` directly — no internal bridging needed.
    """

    def __init__(self, stream_fn: Callable, protocol_id: str = _TEST_API) -> None:
        self._stream_fn = stream_fn
        self._protocol_id = protocol_id

    @property
    def id(self) -> str:
        return self._protocol_id

    @property
    def name(self) -> str:
        return "Test"

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions,
        auth: AuthResult,
    ) -> EventStream[AssistantMessage]:
        return self._stream_fn(model, context, options)


class TestProvider(BaseProvider):
    """Test provider for unit tests."""

    def __init__(self, stream_fn: Callable) -> None:
        self._stream_fn = stream_fn
        self._models: dict[str, Model] = {}
        self._protocol = _TestProtocol(stream_fn)

    @property
    def id(self) -> str:
        return _TEST_PROVIDER

    @property
    def name(self) -> str:
        return "Test"

    def register_model(self, model: Model) -> None:
        self._models[model.id] = model

    def models(self) -> tuple[Model, ...]:
        return tuple(self._models.values())

    def model(self, name: str) -> Model | None:
        full = name if "/" in name else f"{_TEST_PROVIDER}/{name}"
        return self._models.get(full)

    def stream(
        self,
        model_name: str,
        context: Context,
        options: StreamOptions | None = None,
        cancel: None = None,
    ) -> EventStream[AssistantMessage]:
        m = self.model(model_name) or self._resolve(model_name)
        opts = options or StreamOptions()
        if cancel and opts.cancel is None:
            from dataclasses import replace
            opts = replace(opts, cancel=cancel)
        return self._protocol.stream(m, context, opts, AuthResult())

    async def complete(
        self,
        model_name: str,
        context: Context,
        options: StreamOptions | None = None,
        cancel: None = None,
    ) -> AssistantMessage:
        return await self.stream(model_name, context, options, cancel).result()

    async def embed(self, model: str, input: str | tuple[str, ...]) -> EmbeddingResponse:
        raise NotImplementedError("TestProvider does not support embed")

    async def web_search(self, model: str, query: str) -> AssistantMessage:
        raise NotImplementedError("TestProvider does not support web_search")

    async def login(self) -> str:
        return "test-token"

    async def sync_models(self, *, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]:
        return tuple(self._models.values())

    def _resolve(self, name: str) -> Model:
        full = name if "/" in name else f"{_TEST_PROVIDER}/{name}"
        m = self._models.get(full)
        if m is None:
            raise ValueError(f"Unknown model: {name!r}")
        return m


# Convenience functions for existing tests

_test_providers: dict[str, TestProvider] = {}


def register_test_protocol(
    stream_fn: Callable,
    *,
    protocol_id: str = _TEST_API,
) -> None:
    """Register a test provider with the given stream function."""
    tp = TestProvider(stream_fn)
    _test_providers[_TEST_PROVIDER] = tp


def make_test_model(
    model_id: str = "test/dummy",
    *,
    context_window: int = 200_000,
    max_tokens: int = 16_384,
    api: str = _TEST_API,
    provider: str = _TEST_PROVIDER,
    register: bool = True,
) -> Model:
    """Create and register a test model."""
    model = Model(
        id=model_id,
        name=model_id,
        api=api,
        provider=provider,
        context_window=context_window,
        max_tokens=max_tokens,
        cost=ModelCost(),
    )
    if register:
        # Register in test provider if exists
        tp = _test_providers.get(provider)
        if tp:
            tp.register_model(model)
    return model
