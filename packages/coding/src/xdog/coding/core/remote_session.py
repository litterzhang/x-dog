"""Reconnectable remote coding-session protocol surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class RemoteTransport(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def send(self, message: str) -> None: ...
    async def snapshot(self) -> list[dict[str, Any]]: ...


@dataclass
class RemoteSession:
    transport: RemoteTransport
    connected: bool = False

    async def open(self) -> list[dict[str, Any]]:
        await self.transport.connect()
        self.connected = True
        return await self.transport.snapshot()

    async def submit(self, message: str) -> None:
        if not self.connected:
            await self.open()
        await self.transport.send(message)

    async def reconnect(self) -> list[dict[str, Any]]:
        if self.connected:
            await self.transport.close()
        return await self.open()

    async def close(self) -> None:
        if self.connected:
            await self.transport.close()
            self.connected = False
