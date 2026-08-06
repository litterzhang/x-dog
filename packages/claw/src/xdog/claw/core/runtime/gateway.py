"""Gateway daemon — Unix socket server for claw runtime.

Manages the Orchestrator lifecycle and accepts TUI client connections
over a Unix domain socket using a JSON-line protocol.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import stat
from dataclasses import asdict
from pathlib import Path
from typing import Any

from xdog.agent import AgentConfig
from xdog.ai.types import StreamOptions
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput

logger = logging.getLogger(__name__)

# Maximum bytes per JSON-line message from client (1 MB)
_MAX_LINE_LENGTH = 1_048_576


def _build_model_and_options(config: ClawConfig):
    """Resolve the global model name and stream_fn from config.

    Returns ``(model_name, stream_fn)``.
    """
    from xdog.agent.helpers import stream_fn_from_provider
    from xdog.claw.core.runtime.group import resolve_model_name

    model_name = resolve_model_name(config.model)

    # Build stream_fn from ai runtime
    import xdog.ai as ai
    runtime = ai.load()
    sfn = stream_fn_from_provider(runtime) if runtime.active_providers() else None

    return model_name, sfn


def _groups_from_config(config: ClawConfig) -> list[Group]:
    """Convert GroupDef entries from config into Group instances."""
    groups = [
        Group(
            id=gdef.id,
            name=gdef.name,
            is_main=gdef.is_main,
            workspace=gdef.workspace,
            agent_config=AgentConfig(
                model=gdef.model_id,
                options=StreamOptions(
                    thinking=gdef.thinking_level or None,
                    temperature=gdef.temperature,
                    max_tokens=gdef.max_tokens,
                ),
            ),
        )
        for gdef in config.groups
    ]
    return groups or [Group(id="main", name="Claw", is_main=True)]


class GatewayServer:
    """Unix socket server that bridges TUI clients to the Orchestrator."""

    _SCHEDULER_POLL_INTERVAL = 30  # seconds between tick() calls

    def __init__(self, config: ClawConfig) -> None:
        self._config = config
        self._socket_path = Path(config.socket_path)
        self._pid_path = Path(config.pid_file)
        self._server: asyncio.Server | None = None
        self._orchestrator: Orchestrator | None = None
        self._scheduler_task: asyncio.Task | None = None
        self._client_tasks: set[asyncio.Task] = set()
        self._client_counter = 0
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start the gateway: orchestrator + socket server."""
        logger.info("Starting claw gateway...")

        # Initialize orchestrator
        data_dir = Path(self._config.data_dir)
        model, stream_fn = _build_model_and_options(self._config)
        self._orchestrator = Orchestrator(
            self._config,
            model=model, stream_fn=stream_fn,
            data_dir=data_dir,
        )

        # Register groups
        for group in _groups_from_config(self._config):
            self._orchestrator.register_group(group)

        await self._orchestrator.start()

        # Wire WeChat channel if enabled
        if self._config.weixin_enabled:
            try:
                self._start_weixin_channel()
            except Exception:
                logger.exception(
                    "Failed to start WeChat channel — gateway continues without it"
                )

        # Clean up stale socket
        if self._socket_path.exists():
            self._socket_path.unlink()

        # Ensure parent directory exists
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Start Unix socket server
        self._server = await asyncio.start_unix_server(
            self._on_client_connect, path=str(self._socket_path)
        )

        # Restrict socket permissions to owner only (0600)
        os.chmod(str(self._socket_path), stat.S_IRUSR | stat.S_IWUSR)

        # Write PID file
        self._write_pid()

        # Start scheduler polling loop
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info("Gateway listening on %s (PID: %d)", self._socket_path, os.getpid())

    def _on_client_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrap client handler in a tracked task for clean shutdown."""
        self._client_counter += 1
        client_id = self._client_counter
        task = asyncio.create_task(self._handle_client(reader, writer, client_id))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        client_id: int = 0,
    ) -> None:
        """Handle a single TUI client connection."""
        peer = f"client-{client_id}"
        logger.info("Client connected: %s", peer)

        try:
            while not reader.at_eof():
                try:
                    line = await reader.readuntil(b"\n")
                except asyncio.LimitOverrunError:
                    await self._write_response(writer, {
                        "type": "error",
                        "message": "Message too large",
                    })
                    # Drain the oversized line
                    await reader.readline()
                    continue
                except asyncio.IncompleteReadError:
                    break

                if not line:
                    break

                try:
                    request = json.loads(line.decode("utf-8").strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await self._write_response(writer, {
                        "type": "error",
                        "message": "Invalid JSON",
                    })
                    continue

                await self._dispatch(request, writer)

        except (ConnectionResetError, BrokenPipeError):
            logger.info("Client disconnected: %s", peer)
        except Exception:
            logger.exception("Error handling client %s", peer)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("Client session ended: %s", peer)

    async def _dispatch(
        self, request: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        """Route a client request to the appropriate handler."""
        msg_type = request.get("type", "")

        if msg_type == "message":
            await self._handle_chat_message(request, writer)
        elif msg_type == "reset":
            await self._handle_reset(request, writer)
        elif msg_type == "status":
            await self._handle_status(writer)
        elif msg_type == "ping":
            group_id = request.get("group_id", "main")
            session_info: dict[str, Any] = {"type": "pong"}
            if self._orchestrator:
                session_info.update(self._orchestrator.get_session_info(group_id))
                notifications = self._orchestrator.pop_goal_notifications(group_id)
                if notifications:
                    session_info["goal_notifications"] = [
                        asdict(n) for n in notifications
                    ]
            await self._write_response(writer, session_info)
        else:
            await self._write_response(writer, {
                "type": "error",
                "message": f"Unknown message type: {msg_type}",
            })

    async def _handle_chat_message(
        self, request: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        """Process a chat message from the TUI client with streaming deltas."""
        group_id = request.get("group_id", "main")
        content = request.get("content", "")

        if not content:
            await self._write_response(writer, {
                "type": "error",
                "message": "Empty message content",
            })
            return

        if not self._orchestrator:
            await self._write_response(writer, {
                "type": "error",
                "message": "Orchestrator not initialized",
            })
            return

        message = UserInput(
            group_id=group_id,
            content=content,
            sender="user",
            channel="tui",
        )

        # Stream text deltas to the TUI client as they arrive
        accumulated_text = ""
        loop = asyncio.get_running_loop()

        async def _safe_drain() -> None:
            """Drain the writer, silencing errors from a disconnected TUI."""
            try:
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def on_text_delta(delta: str) -> None:
            nonlocal accumulated_text
            accumulated_text += delta
            try:
                line = json.dumps({
                    "type": "delta",
                    "group_id": group_id,
                    "content": accumulated_text,
                    "run_id": "default",
                }) + "\n"
                writer.write(line.encode("utf-8"))
                loop.create_task(_safe_drain())
            except Exception:
                pass

        result = await self._orchestrator.route_message(
            message,
            on_text_delta=on_text_delta,
        )

        if result is None:
            await self._write_response(writer, {
                "type": "error",
                "group_id": group_id,
                "message": f"Unknown group: {group_id}",
            })
        elif result.error:
            await self._write_response(writer, {
                "type": "error",
                "group_id": group_id,
                "message": result.error,
            })
        else:
            if accumulated_text:
                # Deltas already delivered the content — send completion
                # signal only to avoid the TUI rendering the text twice.
                resp: dict[str, Any] = {
                    "type": "final",
                    "group_id": group_id,
                    "run_id": "default",
                }
            else:
                # No streaming happened — send the full content
                resp = {
                    "type": "response",
                    "group_id": group_id,
                    "content": result.response_text or "(no response)",
                    "run_id": "default",
                }
            if result.usage:
                resp["usage"] = result.usage
            await self._write_response(writer, resp)

    async def _handle_reset(
        self, request: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        """Reset a group's session."""
        group_id = request.get("group_id", "main")

        if not self._orchestrator:
            await self._write_response(writer, {
                "type": "error",
                "message": "Orchestrator not initialized",
            })
            return

        if self._orchestrator.reset_group(group_id):
            new_session_info: dict[str, Any] = {
                "type": "reset_ack",
                "group_id": group_id,
            }
            sid = self._orchestrator.get_new_session_id(group_id)
            if sid:
                new_session_info["session_id"] = sid
            await self._write_response(writer, new_session_info)
        else:
            await self._write_response(writer, {
                "type": "error",
                "message": f"Unknown group: {group_id}",
            })

    async def _handle_status(self, writer: asyncio.StreamWriter) -> None:
        """Return gateway status."""
        groups = self._orchestrator.get_group_ids() if self._orchestrator else []

        await self._write_response(writer, {
            "type": "status",
            "running": True,
            "pid": os.getpid(),
            "groups": groups,
        })

    async def _write_response(
        self, writer: asyncio.StreamWriter, data: dict[str, Any]
    ) -> None:
        """Write a JSON-line response to the client."""
        try:
            line = json.dumps(data) + "\n"
            writer.write(line.encode("utf-8"))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def _write_pid(self) -> None:
        """Write current PID to file."""
        self._pid_path.parent.mkdir(parents=True, exist_ok=True)
        self._pid_path.write_text(str(os.getpid()), encoding="utf-8")

    def _remove_pid(self) -> None:
        """Remove PID file."""
        try:
            if self._pid_path.exists():
                self._pid_path.unlink()
        except OSError:
            pass

    async def _scheduler_loop(self) -> None:
        """Periodically call orchestrator.tick() for scheduling and goal runner."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self._SCHEDULER_POLL_INTERVAL)
                if self._orchestrator is not None and not self._shutdown_event.is_set():
                    await self._orchestrator.tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduler loop error")

    def _start_weixin_channel(self) -> None:
        """Initialize and add the WeChat channel to the orchestrator."""
        from xdog.claw.channels.weixin import WeixinChannel
        from xdog.claw.channels.weixin.auth import (
            DEFAULT_BASE_URL,
            load_account,
        )

        config = self._config
        state_dir = Path(config.data_dir)

        account_id = config.weixin_account_id
        token = config.weixin_token
        base_url = config.weixin_base_url

        # Try loading token from account file if not in config
        if account_id and not token:
            account_data = load_account(state_dir, account_id)
            if account_data:
                token = account_data.token
                base_url = base_url or account_data.base_url

        if not token:
            logger.warning(
                "WeChat channel enabled but no token configured. "
                "Run 'xdog-claw channel login --weixin' first."
            )
            return

        base_url = base_url or DEFAULT_BASE_URL

        weixin = WeixinChannel(
            state_dir=state_dir,
            account_id=account_id,
            base_url=base_url,
            token=token,
        )
        if self._orchestrator is None:
            # Every other use in this file guards; this one did not, so adding
            # the channel before `start()` raised AttributeError instead of
            # saying what was wrong.
            logger.error("Cannot add the WeChat channel: the orchestrator is not running")
            return

        self._orchestrator.add_channel(weixin)
        # Channel was added after orchestrator.start(), so connect manually
        asyncio.get_event_loop().create_task(weixin.connect())
        logger.info(
            "WeChat channel added: account=%s base_url=%s",
            account_id,
            base_url,
        )

    async def stop(self) -> None:
        """Gracefully shut down the gateway."""
        logger.info("Shutting down gateway...")

        # Signal shutdown first so loops can exit
        self._shutdown_event.set()

        # Cancel scheduler loop
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        # Stop accepting new connections
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Cancel all active client handler tasks
        for task in list(self._client_tasks):
            task.cancel()
        if self._client_tasks:
            await asyncio.gather(*self._client_tasks, return_exceptions=True)
            self._client_tasks.clear()

        if self._orchestrator:
            await self._orchestrator.stop()

        # Clean up socket and PID files
        try:
            if self._socket_path.exists():
                self._socket_path.unlink()
        except OSError:
            pass

        self._remove_pid()

        logger.info("Gateway stopped.")

    async def run_forever(self) -> None:
        """Run the gateway until interrupted."""
        await self.start()

        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("Received shutdown signal")
            asyncio.ensure_future(self.stop())

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        await self._shutdown_event.wait()


def read_pid(pid_path: Path) -> int | None:
    """Read PID from file, returning None if invalid or missing."""
    try:
        if not pid_path.exists():
            return None
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        return None


def run_gateway(config: ClawConfig) -> None:
    """Entry point to run the gateway (blocking)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    server = GatewayServer(config)
    asyncio.run(server.run_forever())
