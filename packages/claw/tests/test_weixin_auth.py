"""Tests for WeChat auth (account storage + QR login)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from xdog.claw.channels.weixin.auth import (
    WeixinAccountData,
    save_account,
    start_qr_login,
    wait_qr_login,
)


def test_save_account_permissions(tmp_path):
    """Saved account file should have restricted permissions."""
    import os
    import stat

    data = WeixinAccountData(token="secret")
    save_account(tmp_path, "perm-test", data)

    path = tmp_path / "weixin" / "accounts" / "perm-test.json"
    mode = os.stat(path).st_mode
    assert mode & stat.S_IROTH == 0  # no world-read
    assert mode & stat.S_IWOTH == 0  # no world-write

def _make_mock_httpx_client(mock_resp):
    """Create a mock httpx.AsyncClient that works as an async context manager."""
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    async def aenter(*args, **kwargs):
        return mock_client

    async def aexit(*args, **kwargs):
        return False

    mock_client.__aenter__ = aenter
    mock_client.__aexit__ = aexit
    return mock_client

def _make_mock_response(json_data, status_code=200):
    """Create a mock httpx response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=json_data)
    return mock_resp

@pytest.mark.asyncio
async def test_start_qr_login_success():
    """Test QR login start with mocked HTTP."""
    mock_response = {
        "qrcode": "test-qr-code",
        "qrcode_img_content": "https://example.com/qr.png",
    }
    mock_resp = _make_mock_response(mock_response)
    mock_client = _make_mock_httpx_client(mock_resp)

    with patch("xdog.claw.channels.weixin.auth.httpx.AsyncClient", return_value=mock_client):
        result = await start_qr_login(api_base_url="https://test.example.com")

    assert result.qrcode_url == "https://example.com/qr.png"
    assert result.qrcode == "test-qr-code"
    assert result.message

@pytest.mark.asyncio
async def test_start_qr_login_failure():
    """Test QR login start with network error."""
    import httpx

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    async def aenter(*a, **kw):
        return mock_client

    async def aexit(*a, **kw):
        return False

    mock_client.__aenter__ = aenter
    mock_client.__aexit__ = aexit

    with patch("xdog.claw.channels.weixin.auth.httpx.AsyncClient", return_value=mock_client):
        result = await start_qr_login(api_base_url="https://unreachable.example.com")

    assert not result.qrcode_url
    assert "Failed" in result.message

@pytest.mark.asyncio
async def test_wait_qr_login_confirmed():
    """Test QR login wait with confirmed status."""
    mock_status_response = {
        "status": "confirmed",
        "bot_token": "bot-token-xyz",
        "ilink_bot_id": "botid@im.bot",
        "baseurl": "https://ilinkai.weixin.qq.com",
        "ilink_user_id": "user123@im.wechat",
    }
    mock_resp = _make_mock_response(mock_status_response)
    mock_client = _make_mock_httpx_client(mock_resp)

    with patch("xdog.claw.channels.weixin.auth.httpx.AsyncClient", return_value=mock_client):
        result = await wait_qr_login(
            api_base_url="https://test.example.com",
            qrcode="test-qr-code",
            timeout_s=5,
        )

    assert result.connected is True
    assert result.bot_token == "bot-token-xyz"
    assert result.account_id == "botid@im.bot"
    assert result.user_id == "user123@im.wechat"

@pytest.mark.asyncio
async def test_wait_qr_login_expired():
    """Test QR login wait with expired status."""
    mock_status_response = {"status": "expired"}
    mock_resp = _make_mock_response(mock_status_response)
    mock_client = _make_mock_httpx_client(mock_resp)

    with patch("xdog.claw.channels.weixin.auth.httpx.AsyncClient", return_value=mock_client):
        result = await wait_qr_login(
            api_base_url="https://test.example.com",
            qrcode="test-qr-code",
            timeout_s=5,
        )

    assert result.connected is False
    assert "过期" in result.message
