"""The token exchange's failure path.

A stored GitHub OAuth token is long-lived but not permanent. When it stops
working the remedy is a login, and only a human can perform it -- so the
failure has to say so. It previously escaped as a bare ``HTTPStatusError``
that the proxy rendered as a 500 ``api_error``, which reads as "the proxy is
broken" and sends the user looking in the wrong place.
"""
from __future__ import annotations

import httpx
import pytest
from xdog.ai.types import AuthExpiredError
from xdog.ai.vendors.copilot import _exchange_copilot_token


def _client_returning(status: int, payload: dict[str, object] | None = None):
    """An httpx.AsyncClient stand-in whose GET yields *status*."""
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None):
            return httpx.Response(
                status,
                json=payload if payload is not None else {},
                request=httpx.Request("GET", url),
            )

    return _Client


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_rejected_credentials_name_the_fix(monkeypatch, status):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client_returning(status)())

    with pytest.raises(AuthExpiredError) as excinfo:
        await _exchange_copilot_token("ghu_stale")

    message = str(excinfo.value)
    # The remedy has to be in the message the user actually sees.
    assert "xdog-ai login" in message
    assert str(status) in message


@pytest.mark.asyncio
async def test_other_failures_are_not_reported_as_a_login_problem(monkeypatch):
    """A 500 upstream is GitHub's outage, not the user's credentials.

    Telling someone to log in again when logging in cannot help is worse than
    a generic error, because they will do it and still be broken.
    """
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client_returning(500)())

    with pytest.raises(httpx.HTTPStatusError):
        await _exchange_copilot_token("ghu_fine")


@pytest.mark.asyncio
async def test_successful_exchange_never_logs_the_token(monkeypatch, caplog):
    secret = "tid=SECRETTOKENVALUE"
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: _client_returning(200, {
            "token": secret,
            "expires_at": 1700000000,
            "endpoints": {"api": "https://api.example.com"},
        })(),
    )

    with caplog.at_level("INFO"):
        creds = await _exchange_copilot_token("ghu_fine")

    assert creds["token"] == secret
    assert creds["expires"] == 1700000000 * 1000  # seconds → ms, as get_token compares
    assert "1700000000" in caplog.text  # the refresh is visible...
    assert secret not in caplog.text    # ...the credential is not
