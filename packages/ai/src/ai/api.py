"""Public API — provider, load, login."""

from __future__ import annotations

import json
import logging

from ai.core import BaseProvider
from ai.paths import auth_file
from ai.providers import provider as _get_provider
from ai.providers.runtime import Runtime
from ai.types import ProviderType

logger = logging.getLogger(__name__)


def provider(provider_id: str | ProviderType) -> BaseProvider:
    """Get a provider instance."""
    return _get_provider(str(provider_id))


def load() -> Runtime:
    """Load a Runtime that discovers active providers from auth storage."""
    rt = Runtime()
    path = auth_file()

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for pid in data:
                    if pid in ProviderType.__members__.values():
                        rt.add_provider(pid)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read auth.json: %s", exc)

    return rt


async def login(provider_id: str | ProviderType = ProviderType.COPILOT) -> str:
    """Authenticate with a provider."""
    p = _get_provider(str(provider_id))
    return await p.login()
