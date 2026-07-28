"""Reusable OAuth helpers — device code flow, PKCE, token exchange."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def generate_code_verifier(length: int = 128) -> str:
    length = max(43, min(128, length))
    return base64.urlsafe_b64encode(secrets.token_bytes(96)).decode("ascii").rstrip("=")[:length]


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# ---------------------------------------------------------------------------
# Device code flow types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceCodeResponse:
    device_code: str = ""
    user_code: str = ""
    verification_uri: str = ""
    expires_in: int = 0
    interval: int = 5


# ---------------------------------------------------------------------------
# Generic device code flow
# ---------------------------------------------------------------------------

async def device_code_flow(
    *,
    device_code_url: str,
    access_token_url: str,
    client_id: str,
    scope: str = "",
) -> tuple[DeviceCodeResponse, str]:
    """Run a full OAuth device code flow.

    Returns ``(device_response, access_token)``.
    Prints instructions to stderr for the user.
    """
    import asyncio
    import sys

    import httpx

    # Step 1: Start device flow
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            device_code_url,
            json={"client_id": client_id, "scope": scope},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    device = DeviceCodeResponse(
        device_code=data.get("device_code", ""),
        user_code=data.get("user_code", ""),
        verification_uri=data.get("verification_uri", ""),
        expires_in=data.get("expires_in", 0),
        interval=data.get("interval", 5),
    )

    print(
        f"\nOpen {device.verification_uri} and enter code: {device.user_code}\n",
        file=sys.stderr,
    )

    # Step 2: Poll for token
    interval = device.interval
    deadline = time.time() + device.expires_in

    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            await asyncio.sleep(interval)
            resp = await client.post(
                access_token_url,
                json={
                    "client_id": client_id,
                    "device_code": device.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )
            data = resp.json()
            error = data.get("error")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval = data.get("interval", interval + 5)
                continue
            elif error:
                raise RuntimeError(f"OAuth error: {error}: {data.get('error_description', '')}")
            token = data.get("access_token", "")
            if token:
                print("Authentication successful!", file=sys.stderr)
                return (device, token)

    raise TimeoutError("Device code flow timed out")
