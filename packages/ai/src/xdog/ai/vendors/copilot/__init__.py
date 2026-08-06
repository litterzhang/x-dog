"""Copilot vendor — GitHub Copilot authentication and model sync.

Implements :class:`~ai.core.BaseVendor`:
- ``resolve_auth(model)`` → :class:`~ai.core.AuthResult`
- ``sync_models()`` → tuple of :class:`~ai.types.Model`
- ``login()`` → OAuth device code flow
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from xdog.ai.core import AuthResult, BaseVendor

if TYPE_CHECKING:
    from xdog.ai.types import Context, Model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Copilot static headers
# ---------------------------------------------------------------------------

COPILOT_HEADERS: dict[str, str] = {
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Plugin-Version": "copilot-chat/0.26.4",
    "Editor-Version": "vscode/1.99.2",
    "User-Agent": "GitHubCopilotChat/0.26.4",
}

_GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"


# ---------------------------------------------------------------------------
# Dynamic headers
# ---------------------------------------------------------------------------

def _build_dynamic_headers(context: Context) -> dict[str, str]:
    from xdog.ai.types import ImageContent, ToolResultMessage, UserMessage

    messages = context.messages
    initiator = "user"
    if messages:
        initiator = "user" if isinstance(messages[-1], UserMessage) else "agent"

    headers = {"X-Initiator": initiator, "Openai-Intent": "conversation-edits"}

    for msg in messages:
        if isinstance(msg, (UserMessage, ToolResultMessage)) and isinstance(msg.content, tuple):
            if any(isinstance(part, ImageContent) for part in msg.content):
                headers["Copilot-Vision-Request"] = "true"
                break

    return headers


# ---------------------------------------------------------------------------
# Token manager (singleton)
# ---------------------------------------------------------------------------

class _TokenManager:
    """Two-tier token: GitHub OAuth (disk) → Copilot JWT (memory)."""

    def __init__(self) -> None:
        self._github_token: str | None = None
        self._jwt: str | None = None
        self._jwt_expires: float = 0.0
        self._base_url: str | None = None

    async def get_token(self) -> tuple[str, str | None]:
        """Return (jwt, base_url)."""
        now_ms = time.time() * 1000

        if self._jwt and now_ms < self._jwt_expires:
            return (self._jwt, self._base_url)

        if self._github_token:
            return await self._exchange(self._github_token)

        saved = self._load_token()
        if saved:
            self._github_token = saved
            return await self._exchange(saved)

        token = await _login_github()
        self._github_token = token
        self._save_token(token)
        return await self._exchange(token)

    async def _exchange(self, github_token: str) -> tuple[str, str | None]:
        creds = await _exchange_copilot_token(github_token)
        self._jwt = creds["token"]
        self._jwt_expires = creds["expires"]
        self._base_url = creds.get("base_url")
        return (self._jwt, self._base_url)

    def _load_token(self) -> str | None:
        from xdog.ai.paths import auth_file
        f = auth_file()
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                token = data.get("copilot", {}).get("access_token", "")
                if token:
                    return token
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def _save_token(self, github_token: str) -> None:
        from xdog.ai.paths import auth_file, data_dir
        f = auth_file()
        data_dir().mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if f.exists():
            try:
                existing = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        existing["copilot"] = {"type": "oauth", "access_token": github_token, "token_type": "bearer"}
        f.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


_token_manager: _TokenManager | None = None


def _get_token_manager() -> _TokenManager:
    global _token_manager
    if _token_manager is None:
        _token_manager = _TokenManager()
    return _token_manager


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

async def _login_github(domain: str = "github.com") -> str:
    from xdog.ai.utils.auth import device_code_flow
    _, token = await device_code_flow(
        device_code_url=f"https://{domain}/login/device/code",
        access_token_url=f"https://{domain}/login/oauth/access_token",
        client_id=_GITHUB_CLIENT_ID,
        scope="user:email",
    )
    return token


async def _exchange_copilot_token(github_token: str) -> dict[str, Any]:
    import httpx

    # A generous timeout: httpx defaults to 5s, which the GitHub Copilot token
    # exchange can exceed on a cold TLS handshake or a slow network (observed as
    # a spurious ReadTimeout that aborts the first web_search of a session).
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(
            "https://api.github.com/copilot_internal/v2/token",
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/json",
                **COPILOT_HEADERS,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    expires_at = data.get("expires_at", 0)
    endpoints = data.get("endpoints", {})
    return {
        "token": data.get("token", ""),
        "expires": (expires_at * 1000) if isinstance(expires_at, (int, float)) else 0.0,
        "base_url": endpoints.get("api") if isinstance(endpoints, dict) else None,
    }


# ---------------------------------------------------------------------------
# CopilotVendor
# ---------------------------------------------------------------------------

class CopilotVendor(BaseVendor):
    """GitHub Copilot vendor — auth + model sync."""

    @property
    def id(self) -> str:
        return "copilot"

    @property
    def name(self) -> str:
        return "GitHub Copilot"

    async def resolve_auth(self, model: Model, context: Context | None = None) -> AuthResult:
        manager = _get_token_manager()
        jwt, base_url = await manager.get_token()
        dynamic = _build_dynamic_headers(context) if context else {}
        return AuthResult(
            api_key=jwt,
            headers={**COPILOT_HEADERS, **dynamic},
            base_url=base_url or "",
        )

    async def sync_models(self, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]:
        from xdog.ai.vendors.copilot._model_sync import sync_models
        return await sync_models(ttl=ttl, force=force)

    async def login(self) -> str:
        token = await _login_github()
        _get_token_manager()._save_token(token)
        _get_token_manager()._github_token = token
        return token
