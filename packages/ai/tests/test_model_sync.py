"""Tests for ai.vendors.copilot._model_sync — dynamic model synchronisation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai.vendors.copilot._model_sync import (
    _model_from_dict,
    _model_to_dict,
    _parse_api_model,
    _parse_api_response,
    _read_cache,
    _write_cache,
    get_synced_model,
    list_models,
    sync_models,
)
from ai.types import Model, ModelCost, OpenAICompletionsCompat, ThinkingBudgetRange

def _make_api_model(model_id="claude-sonnet-4.6", vendor="Anthropic",
                    context_window=200_000, max_output=32_000,
                    vision=True, adaptive_thinking=True,
                    reasoning_effort=None, endpoints=None) -> dict[str, Any]:
    if reasoning_effort is None:
        reasoning_effort_val = ["low", "medium", "high"]
    elif reasoning_effort is False:
        reasoning_effort_val = None
    else:
        reasoning_effort_val = reasoning_effort
    if endpoints is None:
        endpoints = ["/chat/completions", "/v1/messages"]

    return {
        "id": model_id, "name": f"Model {model_id}", "vendor": vendor,
        "object": "model", "preview": False, "supported_endpoints": endpoints,
        "capabilities": {
            "family": model_id, "type": "chat", "tokenizer": "o200k_base",
            "limits": {"max_context_window_tokens": context_window, "max_output_tokens": max_output},
            "supports": {
                "vision": vision, "tool_calls": True, "streaming": True,
                "adaptive_thinking": adaptive_thinking,
                "reasoning_effort": reasoning_effort_val,
                "max_thinking_budget": 32000,
            },
        },
    }

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cache round-trip
# ---------------------------------------------------------------------------

def test_full_cache_round_trip():
    """All fields survive serialize -> deserialize."""
    original = Model(
        id="copilot/test", name="Test", api="openai-completions", provider="copilot",
        reasoning=True, cost=ModelCost(input=0.0, output=0.0),
        context_window=200_000, max_tokens=32_000,
        compat=OpenAICompletionsCompat(supports_store=True, supports_strict_mode=True),
        thinking_budget_range=ThinkingBudgetRange(min_budget=512, max_budget=64000),
        vendor="Anthropic",
    )
    restored = _model_from_dict(_model_to_dict(original))
    assert restored.id == original.id
    assert restored.reasoning == original.reasoning
    assert restored.thinking_budget_range == original.thinking_budget_range
    assert restored.compat.supports_strict_mode is True

def test_cache_file_write_and_read(tmp_path: Path):
    models = (Model(id="copilot/t1", name="T1"), Model(id="copilot/t2", name="T2"))
    cache_file = tmp_path / "models_cache.json"
    with patch("ai.vendors.copilot._model_sync._CACHE_FILE", cache_file), \
         patch("ai.vendors.copilot._model_sync.data_dir", return_value=tmp_path):
        _write_cache(models)
        result = _read_cache()
    assert result is not None
    assert len(result[0]) == 2

# ---------------------------------------------------------------------------
# sync / list / get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_returns_cached_when_fresh(tmp_path: Path):
    cache_file = tmp_path / "c.json"
    payload = {"timestamp": time.time(), "models": [_model_to_dict(Model(id="copilot/cached", name="C"))]}
    cache_file.write_text(json.dumps(payload))
    with patch("ai.vendors.copilot._model_sync._CACHE_FILE", cache_file):
        result = await sync_models(ttl=3600)
    assert result[0].id == "copilot/cached"

def test_list_models_returns_generated_when_no_cache(tmp_path: Path):
    with patch("ai.vendors.copilot._model_sync._CACHE_FILE", tmp_path / "nope.json"):
        assert len(list_models()) > 0


def test_list_models_returns_stale_cache_over_fallback(tmp_path: Path):
    """A stale (past-TTL) cache with real models is preferred over the fallback.

    Guards the stale-while-error behaviour: a transient sync failure must not
    collapse consumers down to the tiny hard-coded fallback set when a real,
    if stale, model list is on disk.
    """
    cache_file = tmp_path / "c.json"
    stale_ts = time.time() - (48 * 60 * 60)  # 48h old, well past the 24h TTL
    models = [_model_to_dict(Model(id=f"copilot/stale-{i}", name=f"S{i}")) for i in range(12)]
    cache_file.write_text(json.dumps({"timestamp": stale_ts, "models": models}))
    with patch("ai.vendors.copilot._model_sync._CACHE_FILE", cache_file):
        result = list_models()
    ids = {m.id for m in result}
    assert "copilot/stale-0" in ids  # the stale cache is used
    assert len(result) == 12


def test_get_synced_model():
    assert get_synced_model("copilot/claude-sonnet-4.5") is not None
    assert get_synced_model("copilot/nonexistent") is None
