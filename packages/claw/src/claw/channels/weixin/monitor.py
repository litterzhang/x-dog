"""Long-poll monitor loop for WeChat getUpdates.

Ported from openclaw-weixin src/monitor/monitor.ts.
Runs as an asyncio background task, calling on_message for each inbound message.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from claw.channels.weixin.api import WeixinApiClient
from claw.channels.weixin.types import (
    MessageType,
    WeixinMessage,
)

logger = logging.getLogger(__name__)

DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_S = 30.0
RETRY_DELAY_S = 2.0
SESSION_EXPIRED_ERRCODE = -14
SESSION_PAUSE_S = 300.0  # 5 minutes


@dataclass(frozen=True)
class MonitorOpts:
    base_url: str
    token: str
    account_id: str
    state_dir: Path
    cancel_event: asyncio.Event
    on_message: Callable[[WeixinMessage], Awaitable[None]]
    api_client: WeixinApiClient | None = None
    long_poll_timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS


def _sync_buf_file(state_dir: Path, account_id: str) -> Path:
    return state_dir / "weixin" / "accounts" / f"{account_id}.sync.json"


def _load_get_updates_buf(state_dir: Path, account_id: str) -> str:
    path = _sync_buf_file(state_dir, account_id)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("get_updates_buf", "")
    except Exception:
        pass
    return ""


def _save_get_updates_buf(state_dir: Path, account_id: str, buf: str) -> None:
    path = _sync_buf_file(state_dir, account_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"get_updates_buf": buf}), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to save sync buf: %s", exc)


def _is_user_message(msg: WeixinMessage) -> bool:
    """Filter to only process user-sent messages (not bot echoes)."""
    return msg.message_type == MessageType.USER


async def run_monitor(opts: MonitorOpts) -> None:
    """Run the long-poll getUpdates loop until cancel_event is set.

    On new user messages: extracts content, invokes ``on_message`` callback.
    Handles errors with retry/backoff.
    """
    get_updates_buf = _load_get_updates_buf(opts.state_dir, opts.account_id)
    if get_updates_buf:
        logger.info(
            "Monitor resuming from previous sync buf (%d bytes)",
            len(get_updates_buf),
        )
    else:
        logger.info("Monitor starting fresh (no previous sync buf)")

    # Use shared client if provided, else create a local one
    owns_client = opts.api_client is None
    if opts.api_client is not None:
        api_client = opts.api_client
    else:
        api_client = WeixinApiClient(opts.base_url, opts.token)

    next_timeout_ms = opts.long_poll_timeout_ms
    consecutive_failures = 0

    try:
        while not opts.cancel_event.is_set():
            try:
                resp = await api_client.get_updates(
                    get_updates_buf=get_updates_buf,
                    timeout_ms=next_timeout_ms,
                )

                # Update poll timeout if server suggests one
                if resp.longpolling_timeout_ms > 0:
                    next_timeout_ms = resp.longpolling_timeout_ms

                # Check for API errors
                is_api_error = (resp.ret != 0) or (resp.errcode != 0)
                if is_api_error:
                    if resp.errcode == SESSION_EXPIRED_ERRCODE or resp.ret == SESSION_EXPIRED_ERRCODE:
                        logger.error(
                            "getUpdates: session expired (errcode=%d), pausing %ds",
                            resp.errcode,
                            SESSION_PAUSE_S,
                        )
                        consecutive_failures = 0
                        await _cancellable_sleep(SESSION_PAUSE_S, opts.cancel_event)
                        continue

                    consecutive_failures += 1
                    logger.error(
                        "getUpdates failed: ret=%d errcode=%d errmsg=%s (%d/%d)",
                        resp.ret,
                        resp.errcode,
                        resp.errmsg,
                        consecutive_failures,
                        MAX_CONSECUTIVE_FAILURES,
                    )
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        logger.error(
                            "getUpdates: %d consecutive failures, backing off %ds",
                            MAX_CONSECUTIVE_FAILURES,
                            BACKOFF_DELAY_S,
                        )
                        consecutive_failures = 0
                        await _cancellable_sleep(BACKOFF_DELAY_S, opts.cancel_event)
                    else:
                        await _cancellable_sleep(RETRY_DELAY_S, opts.cancel_event)
                    continue

                # Success — reset failure counter
                consecutive_failures = 0

                # Update sync buf for resumption
                if resp.get_updates_buf:
                    _save_get_updates_buf(
                        opts.state_dir, opts.account_id, resp.get_updates_buf
                    )
                    get_updates_buf = resp.get_updates_buf

                # Process messages
                for msg in resp.msgs:
                    if _is_user_message(msg):
                        logger.info(
                            "Inbound message: from=%s types=%s",
                            msg.from_user_id,
                            ",".join(str(i.type) for i in msg.item_list) or "none",
                        )
                        try:
                            await opts.on_message(msg)
                        except Exception:
                            logger.exception(
                                "Error processing message from %s",
                                msg.from_user_id,
                            )

            except asyncio.CancelledError:
                logger.info("Monitor cancelled")
                return
            except Exception:
                if opts.cancel_event.is_set():
                    logger.info("Monitor stopped (cancelled)")
                    return

                consecutive_failures += 1
                logger.exception(
                    "getUpdates error (%d/%d)",
                    consecutive_failures,
                    MAX_CONSECUTIVE_FAILURES,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
                    await _cancellable_sleep(BACKOFF_DELAY_S, opts.cancel_event)
                else:
                    await _cancellable_sleep(RETRY_DELAY_S, opts.cancel_event)

        logger.info("Monitor ended")
    finally:
        if owns_client:
            await api_client.close()


async def _cancellable_sleep(seconds: float, cancel_event: asyncio.Event) -> None:
    """Sleep that can be interrupted by the cancel event."""
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass  # normal — sleep completed without cancellation
