"""Goal planner — LLM-based planning for goal creation and re-planning.

Invoked only when judgment is needed:
1. **Initial planning** — after a goal is created, write strategy and
   start the first task
2. **Re-planning** — verification failed or stall detected, analyze
   what went wrong and add corrective tasks

Uses ``run_agent_loop()`` directly (FlushRunner pattern). Each invocation
is independent — no stored message history.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from xdog.agent.agent_loop import run_agent_loop
from xdog.agent.core import AgentContext, AgentMessage, AgentTool, AgentToolResult, StreamFn
from xdog.agent.types import AgentLoopConfig
from xdog.ai.types import StreamOptions, TextContent, UserMessage
from xdog.claw.core.planning.goal_workspace import GoalWorkspace
from xdog.claw.core.types import Goal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Planner system prompt
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM = """\
You are a goal planner. You create strategies and task plans for goals.
You do NOT execute tasks — you plan them for the main agent to execute.

{goal_md}

{status_md}

{plan_md}

# Your Tools

## `goal` — Manage tasks
- `goal(action: "add_task", goal_id: "{goal_id}", task_description: "...", \
depends_on: ["task-id-1", "task-id-2"])` — Add a new task. Use depends_on \
to specify which tasks must complete before this one can start. Omit for \
tasks with no dependencies.

## `goal_plan` — Update your plan
- `goal_plan(action: "write_plan", content: "...")` — Write plan.md \
with your strategy, task ordering, risks, and approach
- `goal_plan(action: "append_decision", trigger: "...", decisions: [...])` \
— Append a decision entry to the Decision Log

## `bash` — Probe system state
- Use to check what exists, what failed, what the current state is
- Run tests, check processes, verify assumptions before planning

## `filesystem` — Read and search files
- Read file contents to understand current code and configuration
- Search for patterns to find relevant code
- Use to make informed decisions about task ordering and approach

# Rules

- Write a clear strategy in plan.md before anything else.
- Model task dependencies explicitly with depends_on. If task B needs \
the output of task A, set depends_on: ["a-id"]. Independent tasks with \
no dependency can run in parallel — the system starts them automatically.
- Make task descriptions specific and actionable. Include exact commands \
or steps when possible.
- Do NOT start tasks yourself — the system starts the first task \
automatically after you finish planning.
- When re-planning, explain what went wrong and how the new approach \
differs from the old one."""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PLAN_PROMPT = """\
This goal was just created. Create an initial plan:

1. Analyze the goal, its tasks, and verification criteria.
2. Write a strategy in plan.md — task ordering, risks, approach.
3. Start the first task by marking it in_progress.

Goal ID: {goal_id}"""


_REPLAN_PROMPT = """\
This goal needs re-planning. Reason: {reason}

1. Read the current plan and status carefully.
2. Use bash to check system state if needed — understand what actually happened.
3. Analyze what went wrong and why the current tasks aren't achieving the goal.
4. Add corrective tasks with specific, actionable descriptions.
5. Update plan.md with your analysis and new strategy.
6. Start the first corrective task.

Goal ID: {goal_id}"""


# ---------------------------------------------------------------------------
# Goal plan tool (workspace file updates)
# ---------------------------------------------------------------------------

def create_goal_plan_tool(
    goal_id: str,
    workspace: GoalWorkspace,
) -> AgentTool:
    """Create a tool that lets the planner update plan.md.

    Lightweight, goal-scoped tool — created per-invocation with the
    correct goal_id and workspace bound via closure.
    """
    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        cancel: Any = None,
        on_update: Any = None,
        **kwargs: Any,
    ) -> AgentToolResult:
        action = params.get("action", "")
        try:
            if action == "write_plan":
                content = params.get("content", "")
                if not content:
                    return _text_result("Error: content is required")
                workspace.write_plan(goal_id, content)
                return _text_result("plan.md updated.")

            elif action == "append_decision":
                trigger = params.get("trigger", "")
                decisions = params.get("decisions", [])
                strategy = params.get("strategy", "")
                if not decisions:
                    return _text_result("Error: decisions list is required")
                workspace.append_decision(goal_id, trigger, decisions, strategy)
                return _text_result("Decision appended to plan.md.")

            else:
                return _text_result(
                    f"Error: unknown action '{action}'. "
                    "Use 'write_plan' or 'append_decision'."
                )
        except Exception as exc:
            return _text_result(f"Error: {exc}")

    return AgentTool(
        name="goal_plan",
        description=(
            "Update goal plan and decisions. "
            "[write_plan] Overwrite plan.md. "
            "[append_decision] Append a decision entry to the Decision Log."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["write_plan", "append_decision"],
                    "description": "Action to perform",
                },
                "content": {
                    "type": "string",
                    "description": "(write_plan) Full content to write to plan.md",
                },
                "trigger": {
                    "type": "string",
                    "description": "(append_decision) What triggered this decision",
                },
                "decisions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "(append_decision) List of decisions made",
                },
                "strategy": {
                    "type": "string",
                    "description": "(append_decision) Strategy notes",
                },
            },
            "required": ["action"],
        },
        execute=execute,
    )


def _text_result(text: str) -> AgentToolResult:
    return AgentToolResult(content=(TextContent(text=text),))


async def _noop_emit(event: Any) -> None:
    """Discard all events — planner is a background agent."""


# ---------------------------------------------------------------------------
# GoalPlanner
# ---------------------------------------------------------------------------

class GoalPlanner:
    """LLM-based goal planning. Invoked only for creation and re-planning.

    Uses ``run_agent_loop()`` directly — each call is a single independent
    agent turn with scoped tools (goal, goal_plan, bash, filesystem).

    All calls are bounded by ``AGENT_TIMEOUT`` seconds via an asyncio
    cancel event.

    Reusable — create once per GoalManager, call methods as needed.
    """

    AGENT_TIMEOUT = 300  # seconds — max time for a single planner turn

    def __init__(self, model: str, stream_fn: StreamFn | None = None) -> None:
        self._model = model
        self._stream_fn = stream_fn

    async def plan_goal(
        self,
        goal: Goal,
        workspace: GoalWorkspace,
        tools: list[AgentTool],
        tool_ctx: dict[str, Any],
    ) -> None:
        """Initial planning: analyze goal, write strategy, start first task."""
        prompt = _PLAN_PROMPT.format(goal_id=goal.id)
        await self._run(goal, workspace, tools, tool_ctx, prompt)

    async def replan_goal(
        self,
        goal: Goal,
        reason: str,
        workspace: GoalWorkspace,
        tools: list[AgentTool],
        tool_ctx: dict[str, Any],
    ) -> None:
        """Re-plan after failure: analyze, add corrective tasks, update plan."""
        prompt = _REPLAN_PROMPT.format(goal_id=goal.id, reason=reason)
        await self._run(goal, workspace, tools, tool_ctx, prompt)

    async def _run(
        self,
        goal: Goal,
        workspace: GoalWorkspace,
        tools: list[AgentTool],
        tool_ctx: dict[str, Any],
        user_prompt: str,
    ) -> None:
        """Execute one planner turn with timeout."""
        try:
            sfn = self._stream_fn
            if sfn is None:
                import xdog.ai as ai
                from xdog.agent.helpers import stream_fn_from_provider
                sfn = stream_fn_from_provider(ai.load())

            goal_md = workspace.read_goal(goal.id)
            status_md = workspace.read_status(goal.id)
            plan_md = workspace.read_plan(goal.id)

            system_prompt = _PLANNER_SYSTEM.format(
                goal_md=goal_md,
                status_md=status_md,
                plan_md=plan_md,
                goal_id=goal.id,
            )

            plan_tool = create_goal_plan_tool(goal.id, workspace)
            all_tools = [*tools, plan_tool]

            cancel = asyncio.Event()
            context = AgentContext(
                system_prompt=system_prompt,
                messages=[],
                tools=all_tools,
            )
            prompts: list[AgentMessage] = [UserMessage(content=user_prompt)]

            async def _run_loop() -> None:
                await run_agent_loop(
                    prompts, context, AgentLoopConfig(),
                    _noop_emit, sfn, self._model, StreamOptions(),
                    cancel=cancel, tool_ctx=tool_ctx,
                )

            await asyncio.wait_for(_run_loop(), timeout=self.AGENT_TIMEOUT)

            # Sync status.md after planner may have updated tasks
            manager = tool_ctx.get("_goal_manager")
            if manager:
                updated = manager.tracker.get_goal(goal.id)
                if updated:
                    workspace.write_status(updated)

            logger.info("Goal planner completed: goal=%s", goal.id)

        except asyncio.TimeoutError:
            cancel.set()
            logger.warning(
                "Goal planner timed out after %ds: goal=%s",
                self.AGENT_TIMEOUT, goal.id,
            )
            raise
        except Exception:
            logger.exception("Goal planner failed: goal=%s", goal.id)
            raise  # let GoalManager handle retry
