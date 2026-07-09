"""Goal manager — manages goal lifecycle with state machine + LLM planner.

Owns ``GoalTracker``, ``GoalWorkspace``, ``GoalStateMachine``, and
``GoalPlanner``. Replaces the old trigger-based GoalAgent system.

**How it works:**

1. Main agent updates a task → ``on_task_updated()`` fires synchronously
2. State machine determines the next action (zero LLM cost)
3. GoalManager executes the action:
   - START_TASK: update tracker, send SystemInput to agent via route_fn
   - RUN_VERIFICATION: queue for async execution in tick()
   - COMPLETE_GOAL: update tracker, notify agent
   - REQUEST_PLAN/REPLAN: queue for async LLM call in tick()
4. ``tick()`` processes queued verifications and re-plans (async)

**Communication:** The goal system sends ``SystemInput`` messages through
``route_fn`` (bound to ``Orchestrator.route_message``). This ensures the
main agent actually receives and acts on goal instructions — unlike
``send_fn`` which only pushes text to channels (TUI/WeChat).

Lives in ``GroupRuntime`` alongside MemoryManager and SkillManager.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from agent.agent_loop import run_agent_loop
from agent.core import AgentContext, AgentMessage, AgentTool, AgentToolResult, StreamFn
from agent.types import AgentLoopConfig
from ai.types import StreamOptions, TextContent, UserMessage

from claw.core.planning.goal_planner import GoalPlanner, _noop_emit
from claw.core.planning.goal_state_machine import (
    GoalActionKind, GoalEvent, GoalEventKind, GoalStateMachine,
)
from claw.core.planning.goal_tracker import GoalTracker
from claw.core.planning.goal_workspace import GoalWorkspace
from claw.core.types import (
    Goal, GoalNotification, GoalStatus, SystemInput, SystemInputKind, TaskStatus,
    Verification, VerificationMethod, VerificationResult, VerificationRun,
)

logger = logging.getLogger(__name__)

# Type for the route function: takes a GroupInput, returns TurnResult | None
RouteFn = Callable[..., Awaitable[Any]]


# ---------------------------------------------------------------------------
# Conditions verifier — agent with tools for grounded verification
# ---------------------------------------------------------------------------

_VERIFIER_SYSTEM = """\
You are a verification agent. Your job is to check whether conditions \
for a goal are actually met — not by guessing, but by running commands \
and checking real system state.

{goal_md}

{status_md}

# Conditions to Verify

{conditions_text}

# Your Tools

## `bash` — Run commands to check real state
Use this to verify conditions. Examples:
- `curl -s https://api/health` to check API endpoints
- `pytest tests/ -q` to run tests
- `ls -la /path/to/file` to check files exist
- `git log --oneline -5` to check recent commits

## `filesystem` — Read files
Use to check file contents, configs, code changes.

## `verdict` — Report your finding
After checking all conditions, call this tool with your result.
You MUST call verdict exactly once, as your final action.

# Rules

- Check each condition with real commands, not assumptions.
- If you can't check a condition (e.g. no access), report it as FAILED \
with an explanation of what you tried.
- Call the verdict tool as your final action with the overall result."""


def _build_conditions_text(conditions: tuple[str, ...]) -> str:
    return "\n".join(f"{i}. {c}" for i, c in enumerate(conditions, 1))


def _create_verdict_tool() -> tuple[AgentTool, dict[str, Any]]:
    """Create a verdict tool that captures the verification result.

    Returns (tool, result_holder) — result_holder["result"] is set
    when the tool is called.
    """
    result_holder: dict[str, Any] = {"result": None, "output": None}

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        cancel: Any = None,
        on_update: Any = None,
        **kwargs: Any,
    ) -> AgentToolResult:
        verdict = params.get("verdict", "").upper()
        output = params.get("reasoning", "")

        if verdict == "PASSED":
            result_holder["result"] = VerificationResult.PASSED
        else:
            result_holder["result"] = VerificationResult.FAILED
        result_holder["output"] = output

        return AgentToolResult(
            content=(TextContent(text=f"Verdict recorded: {verdict}"),),
        )

    tool = AgentTool(
        name="verdict",
        description="Report the verification result. Call exactly once as your final action.",
        parameters={
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["PASSED", "FAILED"],
                    "description": "Overall verdict: PASSED only if ALL conditions are met",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Per-condition results and explanation",
                },
            },
            "required": ["verdict", "reasoning"],
        },
        execute=execute,
    )
    return tool, result_holder


# ---------------------------------------------------------------------------
# GoalManager
# ---------------------------------------------------------------------------

class GoalManager:
    """Manages goal lifecycle with deterministic state machine + LLM planner.

    The state machine handles common transitions (zero LLM cost).
    The planner is invoked only for initial planning and re-planning.
    Communication with the main agent uses group_message (send_fn).

    Parameters
    ----------
    goals_file:
        Path to goals.json (GoalTracker persistence).
    goals_dir:
        Directory for goal workspaces (``{data_dir}/goals/``).
    model:
        Model name for planner and conditions verification.
    stream_fn:
        Optional stream function for planner LLM calls.
    send_fn:
        Callback to send messages to agent groups (group_message).
    """

    VERIFICATION_TIMEOUT = 30       # seconds — script subprocess
    AGENT_TIMEOUT = 300             # seconds — verifier/planner agent loops

    def __init__(
        self,
        goals_file: Path,
        goals_dir: Path,
        model: str,
        stream_fn: StreamFn | None = None,
        route_fn: RouteFn | None = None,
    ) -> None:
        self.tracker = GoalTracker(goals_file)
        self.workspace = GoalWorkspace(goals_dir)
        self._state_machine = GoalStateMachine()
        self._planner = GoalPlanner(model, stream_fn)
        self._model = model
        self._route_fn = route_fn

        # Queues for async work (processed in tick())
        self._pending_verifications: list[str] = []           # goal_ids
        self._pending_plans: list[str] = []                   # goal_ids
        self._pending_replans: list[tuple[str, str]] = []     # (goal_id, reason)
        self._pending_messages: list[tuple[str, str]] = []    # (group_id, content)
        # When True, on_task_updated/on_task_added skip the state machine.
        # Set during planner execution so the planner's own tool calls
        # don't trigger autonomous agent turns.
        self._planning_active = False

    # -- Synchronous event handling (called by GoalTool) -------------------

    def on_task_updated(
        self, goal_id: str, task_id: str, status: TaskStatus,
    ) -> None:
        """Called when the main agent updates a task.

        Runs the state machine synchronously and executes the resulting
        action. Suppressed during planner execution — the planner manages
        its own task transitions.
        """
        if self._planning_active:
            # Planner is running — just update workspace, skip state machine
            goal = self.tracker.get_goal(goal_id)
            if goal:
                self.workspace.write_status(goal)
            return

        goal = self.tracker.get_goal(goal_id)
        if not goal or goal.status != GoalStatus.ACTIVE:
            return

        if status == TaskStatus.COMPLETED:
            event = GoalEvent(kind=GoalEventKind.TASK_COMPLETED, task_id=task_id)
        elif status == TaskStatus.SKIPPED:
            event = GoalEvent(kind=GoalEventKind.TASK_FAILED, task_id=task_id)
        else:
            self.workspace.write_status(goal)
            return

        self.workspace.write_status(goal)

        # Process the event — may return START_TASK multiple times
        # as parallel tasks become unblocked
        action = self._state_machine.process_event(goal, event)
        self._execute_action(goal, action)

        # Start additional ready tasks (parallel workstreams)
        if action.kind == GoalActionKind.START_TASK:
            self._start_additional_ready_tasks(goal)

    def on_goal_created(self, goal_id: str) -> None:
        """Called after a new goal is created. Queues initial planning."""
        goal = self.tracker.get_goal(goal_id)
        if not goal:
            return

        event = GoalEvent(kind=GoalEventKind.GOAL_CREATED)
        action = self._state_machine.process_event(goal, event)
        self._execute_action(goal, action)

    def on_task_added(self, goal_id: str) -> None:
        """Called after a new task is added to a goal."""
        if self._planning_active:
            return

        goal = self.tracker.get_goal(goal_id)
        if not goal or goal.status != GoalStatus.ACTIVE:
            return

        event = GoalEvent(kind=GoalEventKind.TASK_ADDED)
        action = self._state_machine.process_event(goal, event)
        self._execute_action(goal, action)

    # -- Action execution --------------------------------------------------

    def _execute_action(self, goal: Goal, action: Any) -> None:
        """Execute a GoalAction returned by the state machine."""
        if action.kind == GoalActionKind.START_TASK:
            task_desc = ""
            for t in goal.tasks:
                if t.id == action.task_id:
                    task_desc = t.description
                    break
            self.tracker.update_task(
                goal.id, action.task_id, TaskStatus.IN_PROGRESS,
            )
            updated = self.tracker.get_goal(goal.id)
            if updated:
                self.workspace.write_status(updated)

            # Build message with strategy context from plan.md
            message = self._build_task_message(goal, action.task_id, task_desc)
            self._queue_agent_message(goal.group_id, message)

        elif action.kind == GoalActionKind.RUN_VERIFICATION:
            self._pending_verifications.append(goal.id)

        elif action.kind == GoalActionKind.COMPLETE_GOAL:
            self.tracker.update_goal_status(
                goal.id, GoalStatus.COMPLETED, summary=action.message,
            )
            self.tracker.add_notification(
                goal.id, goal.title, action.message, "completed",
            )
            self._state_machine.reset_goal(goal.id)
            self._queue_agent_message(
                goal.group_id, f"Goal completed: {goal.title}",
            )

        elif action.kind == GoalActionKind.REQUEST_PLAN:
            self._pending_plans.append(goal.id)

        elif action.kind == GoalActionKind.REQUEST_REPLAN:
            self._pending_replans.append((goal.id, action.reason))

    # -- Async tick (called by Orchestrator) -------------------------------

    async def tick(
        self, tools: list[AgentTool], tool_ctx: dict[str, Any],
    ) -> None:
        """Process queued messages, verifications, and re-plans.

        Called periodically by the Orchestrator (~30s). Delivers pending
        messages as SystemInput via route_fn so the agent actually
        receives them. Then runs async work (verification, planning).
        """
        # 0. Deliver pending messages to the agent via route_fn
        messages = list(self._pending_messages)
        self._pending_messages.clear()
        for group_id, content in messages:
            await self._deliver_to_agent(group_id, content)
        # 1. Run pending verifications
        verifications = list(self._pending_verifications)
        self._pending_verifications.clear()
        for goal_id in verifications:
            goal = self.tracker.get_goal(goal_id)
            if goal and goal.status == GoalStatus.ACTIVE:
                run = await self.run_verification(goal_id, tools, tool_ctx)
                # Feed result back to state machine
                goal = self.tracker.get_goal(goal_id)
                if goal:
                    if run.result == VerificationResult.PASSED:
                        event = GoalEvent(kind=GoalEventKind.VERIFICATION_PASSED)
                    else:
                        event = GoalEvent(
                            kind=GoalEventKind.VERIFICATION_FAILED,
                            detail=run.output[:200],
                        )
                    action = self._state_machine.process_event(goal, event)
                    self._execute_action(goal, action)

        # 2. Run pending initial plans (suppress state machine during planning)
        plans = list(self._pending_plans)
        self._pending_plans.clear()
        for goal_id in plans:
            goal = self.tracker.get_goal(goal_id)
            if goal and goal.status == GoalStatus.ACTIVE:
                self._ensure_workspace(goal)
                scoped_tools = self._scope_tools(tools)
                scoped_ctx = {**tool_ctx, "_goal_manager": self}
                self._planning_active = True
                try:
                    await self._planner.plan_goal(
                        goal, self.workspace, scoped_tools, scoped_ctx,
                    )
                except Exception:
                    logger.exception("Initial planning failed for goal %s", goal_id)
                finally:
                    self._planning_active = False
                # After planning, kick off the first task via state machine
                updated = self.tracker.get_goal(goal_id)
                if updated and updated.status == GoalStatus.ACTIVE:
                    event = GoalEvent(kind=GoalEventKind.TASK_ADDED)
                    action = self._state_machine.process_event(updated, event)
                    self._execute_action(updated, action)

        # 3. Run pending re-plans (suppress state machine during re-planning)
        replans = list(self._pending_replans)
        self._pending_replans.clear()
        for goal_id, reason in replans:
            goal = self.tracker.get_goal(goal_id)
            if goal and goal.status == GoalStatus.ACTIVE:
                self._ensure_workspace(goal)
                scoped_tools = self._scope_tools(tools)
                scoped_ctx = {**tool_ctx, "_goal_manager": self}
                self._planning_active = True
                try:
                    await self._planner.replan_goal(
                        goal, reason, self.workspace, scoped_tools, scoped_ctx,
                    )
                except Exception:
                    logger.exception("Re-planning failed for goal %s", goal_id)
                finally:
                    self._planning_active = False
                # After replanning, kick off the next task
                updated = self.tracker.get_goal(goal_id)
                if updated and updated.status == GoalStatus.ACTIVE:
                    self.workspace.write_status(updated)
                    event = GoalEvent(kind=GoalEventKind.TASK_ADDED)
                    action = self._state_machine.process_event(updated, event)
                    self._execute_action(updated, action)

        # 4. Deliver any new messages queued during steps 1-3
        #    (e.g. re-plan adds corrective tasks → state machine queues START_TASK)
        new_messages = list(self._pending_messages)
        self._pending_messages.clear()
        for group_id, content in new_messages:
            await self._deliver_to_agent(group_id, content)

    # -- Helpers -----------------------------------------------------------

    def _scope_tools(self, tools: list[AgentTool]) -> list[AgentTool]:
        """Filter tools to planner/verifier-relevant ones.

        Planner and verifier get: goal (task management), bash (run commands),
        filesystem (read files, check state).
        """
        allowed = {"goal", "bash", "filesystem"}
        return [t for t in tools if t.name in allowed]

    def _ensure_workspace(self, goal: Goal) -> None:
        """Rebuild workspace from GoalTracker if files are missing."""
        goal_dir = self.workspace.goal_dir(goal.id)
        if not (goal_dir / "goal.md").exists():
            logger.warning(
                "Goal workspace missing for %s, rebuilding", goal.id,
            )
            self.workspace.init_workspace(goal)

    def _queue_agent_message(self, group_id: str, content: str) -> None:
        """Queue a message to be delivered to the agent in the next tick()."""
        self._pending_messages.append((group_id, content))

    def _start_additional_ready_tasks(self, goal: Goal) -> None:
        """Start additional ready tasks for parallel execution.

        Only starts tasks that have explicit ``depends_on`` — tasks
        without deps are treated as sequential (creation order) and
        handled one at a time by the state machine.

        This prevents auto-starting all tasks at once when none have
        dependencies defined.
        """
        from claw.core.planning.goal_state_machine import _ready_tasks
        updated = self.tracker.get_goal(goal.id)
        if not updated:
            return
        ready = _ready_tasks(updated)
        for task in ready:
            # Only auto-start if task explicitly declared deps that are now met
            if not task.depends_on:
                continue
            self.tracker.update_task(goal.id, task.id, TaskStatus.IN_PROGRESS)
            refreshed = self.tracker.get_goal(goal.id)
            if refreshed:
                self.workspace.write_status(refreshed)
            message = self._build_task_message(goal, task.id, task.description)
            self._queue_agent_message(goal.group_id, message)

    def _build_task_message(
        self, goal: Goal, task_id: str, task_desc: str,
    ) -> str:
        """Build a task instruction with strategy context from plan.md."""
        lines = [
            f"[Goal: {goal.title}] Task ready: {task_desc}",
        ]
        # Include strategy excerpt from plan.md
        strategy = self._extract_strategy(goal.id)
        if strategy:
            lines.append(f"\nStrategy context:\n{strategy}")
        lines.append(
            f"\nMark done when complete: goal(action: \"update_task\", "
            f"goal_id: \"{goal.id}\", task_id: \"{task_id}\", "
            f"task_status: \"complete\", summary: \"...\")"
        )
        return "\n".join(lines)

    def _extract_strategy(self, goal_id: str) -> str:
        """Extract the Current Strategy section from plan.md."""
        plan = self.workspace.read_plan(goal_id)
        if not plan:
            return ""
        # Find the "## Current Strategy" section, stop at next ##
        lines = plan.splitlines()
        in_strategy = False
        strategy_lines: list[str] = []
        for line in lines:
            if line.startswith("## Current Strategy"):
                in_strategy = True
                continue
            if in_strategy and line.startswith("## "):
                break
            if in_strategy and line.strip():
                strategy_lines.append(line)
        return "\n".join(strategy_lines).strip()

    async def _deliver_to_agent(self, group_id: str, content: str) -> None:
        """Deliver a message to the agent as a SystemInput via route_fn.

        This goes through Orchestrator.route_message(), which means the
        agent gets a real turn with the message — not just text pushed
        to the TUI.
        """
        if self._route_fn is None:
            logger.warning("No route_fn — cannot deliver goal message to agent")
            return
        try:
            message = SystemInput(
                group_id=group_id,
                content=content,
                kind=SystemInputKind.GOAL_RUNNER,
            )
            await self._route_fn(message)
        except Exception:
            logger.exception("Failed to deliver goal message to agent: %s", content[:80])

    # -- Verification ------------------------------------------------------

    async def run_verification(
        self,
        goal_id: str,
        tools: list[AgentTool] | None = None,
        tool_ctx: dict[str, Any] | None = None,
    ) -> VerificationRun:
        """Run verification for a goal.

        Supports hybrid verification: if both script and conditions are
        defined, runs the script first (fast, definitive). If the script
        passes but conditions exist, runs the conditions verifier agent
        with bash/filesystem tools for grounded checking. Both must pass.
        """
        goal = self.tracker.get_goal(goal_id)
        if not goal:
            return VerificationRun(
                result=VerificationResult.ERROR,
                output="Goal not found",
                timestamp=time.time(),
            )

        v = goal.verification
        script_result: VerificationRun | None = None
        conditions_result: VerificationRun | None = None

        # Run script if defined
        if v.script:
            script_result = await self._run_script_verification(v.script)
            if script_result.result != VerificationResult.PASSED:
                # Script failed — no need to check conditions
                self.tracker.record_verification(goal_id, script_result)
                updated = self.tracker.get_goal(goal_id)
                if updated:
                    self.workspace.write_status(updated)
                return script_result

        # Run conditions if defined (and script passed or wasn't defined)
        if v.conditions:
            conditions_result = await self._run_conditions_verification(
                goal, v.conditions, tools or [], tool_ctx or {},
            )

        # Determine final result
        if script_result and conditions_result:
            # Both ran — combine outputs
            if conditions_result.result != VerificationResult.PASSED:
                run = VerificationRun(
                    result=conditions_result.result,
                    output=f"Script: PASSED\nConditions: {conditions_result.output}",
                    timestamp=time.time(),
                )
            else:
                run = VerificationRun(
                    result=VerificationResult.PASSED,
                    output=f"Script: PASSED\nConditions: PASSED",
                    timestamp=time.time(),
                )
        elif script_result:
            run = script_result
        elif conditions_result:
            run = conditions_result
        else:
            # No verification defined — auto-pass
            run = VerificationRun(
                result=VerificationResult.PASSED,
                output="No verification criteria defined.",
                timestamp=time.time(),
            )

        self.tracker.record_verification(goal_id, run)
        updated = self.tracker.get_goal(goal_id)
        if updated:
            self.workspace.write_status(updated)
        return run

    async def _run_script_verification(self, script: str) -> VerificationRun:
        """Run a bash script. Exit 0 = PASSED."""
        try:
            proc = await asyncio.create_subprocess_shell(
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.VERIFICATION_TIMEOUT,
            )
            output = (stdout + stderr).decode(errors="replace").strip()
            result = (
                VerificationResult.PASSED
                if proc.returncode == 0
                else VerificationResult.FAILED
            )
        except asyncio.TimeoutError:
            result = VerificationResult.ERROR
            output = f"Script timed out after {self.VERIFICATION_TIMEOUT}s"
        except Exception as exc:
            result = VerificationResult.ERROR
            output = str(exc)
        return VerificationRun(result=result, output=output, timestamp=time.time())

    async def _run_conditions_verification(
        self,
        goal: Goal,
        conditions: tuple[str, ...],
        tools: list[AgentTool],
        tool_ctx: dict[str, Any],
    ) -> VerificationRun:
        """Run agent-based conditions verification with real tool access.

        The verifier agent gets bash and filesystem tools to actually
        check conditions (curl endpoints, run tests, read files), plus
        a verdict tool to report its structured finding.
        """
        if not conditions:
            return VerificationRun(
                result=VerificationResult.PASSED,
                output="No conditions defined — auto-pass.",
                timestamp=time.time(),
            )

        try:
            sfn = self._planner._stream_fn
            if sfn is None:
                import ai
                from agent.helpers import stream_fn_from_provider
                sfn = stream_fn_from_provider(ai.load())

            goal_md = self.workspace.read_goal(goal.id)
            status_md = self.workspace.read_status(goal.id)

            system_prompt = _VERIFIER_SYSTEM.format(
                goal_md=goal_md,
                status_md=status_md,
                conditions_text=_build_conditions_text(conditions),
            )

            verdict_tool, result_holder = _create_verdict_tool()
            verifier_tools = [
                t for t in tools if t.name in ("bash", "filesystem")
            ]
            verifier_tools.append(verdict_tool)

            cancel = asyncio.Event()
            context = AgentContext(
                system_prompt=system_prompt,
                messages=[],
                tools=verifier_tools,
            )
            prompt = "Verify each condition by running commands and checking real state. Call verdict when done."
            prompts: list[AgentMessage] = [UserMessage(content=prompt)]

            async def _run_loop() -> None:
                await run_agent_loop(
                    prompts, context, AgentLoopConfig(),
                    _noop_emit, sfn, self._model, StreamOptions(),
                    cancel=cancel, tool_ctx=tool_ctx,
                )

            await asyncio.wait_for(_run_loop(), timeout=self.AGENT_TIMEOUT)

            if result_holder["result"] is not None:
                result = result_holder["result"]
                output = result_holder["output"] or ""
            else:
                result = VerificationResult.FAILED
                output = "Verification agent did not report a verdict."

        except asyncio.TimeoutError:
            cancel.set()
            logger.warning(
                "Conditions verifier timed out after %ds for goal %s",
                self.AGENT_TIMEOUT, goal.id,
            )
            result = VerificationResult.ERROR
            output = f"Verification agent timed out after {self.AGENT_TIMEOUT}s"
        except Exception as exc:
            logger.exception("Conditions verification failed for goal %s", goal.id)
            result = VerificationResult.ERROR
            output = str(exc)

        return VerificationRun(
            result=result, output=output, timestamp=time.time(),
        )

    # -- Facades (for GroupRuntime) -----------------------------------------

    def has_active_goals(self) -> bool:
        return bool(self.tracker.list_goals(status_filter="active"))

    def has_pending_work(self) -> bool:
        """True if there are queued messages, verifications, plans, or re-plans."""
        return bool(
            self._pending_messages
            or self._pending_verifications
            or self._pending_plans
            or self._pending_replans
        )

    def goals_summary(self) -> str:
        return self.tracker.build_active_summary()

    def pop_notifications(self) -> list[GoalNotification]:
        return self.tracker.pop_notifications()
