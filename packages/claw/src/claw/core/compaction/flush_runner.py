"""Flush runner — pre-compaction memory flush via direct agent loop.

Before compaction summarizes and discards old context, the flush runner
executes a silent agent turn that saves durable facts to long-term memory
and the daily log.  Uses ``run_agent_loop()`` directly — no Agent wrapper
needed since we don't need state management, event subscriptions, or queues.
"""
from __future__ import annotations

import logging
from typing import Any

from agent.agent_loop import run_agent_loop
from agent.core import AgentContext, AgentMessage, AgentTool, StreamFn
from agent.types import AgentLoopConfig
from ai.types import StreamOptions, UserMessage

from claw.core.compaction.prompts import build_flush_prompt

logger = logging.getLogger(__name__)


async def _noop_emit(event: Any) -> None:
    """Discard all events from the flush turn."""
    pass


class FlushRunner:
    """Runs a silent flush turn to save durable context before compaction.

    Reusable — create once per group, call ``run()`` each time compaction fires.
    """

    def __init__(self, model: str, stream_fn: StreamFn | None = None) -> None:
        self._model = model
        self._stream_fn = stream_fn

    async def run(
        self,
        messages: list[AgentMessage],
        system_prompt: str,
        tools: list[AgentTool],
    ) -> None:
        """Execute a silent flush turn with access to memory tools."""
        try:
            sfn = self._stream_fn
            if sfn is None:
                import ai
                from agent.helpers import stream_fn_from_provider
                sfn = stream_fn_from_provider(ai.load())

            context = AgentContext(
                system_prompt=system_prompt,
                messages=list(messages),
                tools=tools,
            )
            prompts: list[AgentMessage] = [UserMessage(content=build_flush_prompt())]

            await run_agent_loop(
                prompts, context, AgentLoopConfig(), _noop_emit,
                sfn, self._model, StreamOptions(),
            )
        except Exception:
            logger.exception("Pre-compaction flush failed")
