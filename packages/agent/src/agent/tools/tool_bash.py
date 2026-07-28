from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ai.types import TextContent

from agent.core import AgentTool, AgentToolResult
from agent.tools._utils import (
    _BASH_TEMP_THRESHOLD,
    _DEFAULT_BASH_TIMEOUT_MS,
    _MAX_BASH_TIMEOUT_MS,
    kill_process_tree,
    truncate,
    try_update_cwd,
)


def create_bash_tool(*, initial_cwd: Path | None = None) -> AgentTool:
    """Create a tool that executes bash commands with stateful CWD tracking.

    The working directory persists between calls. If a command contains
    ``cd``, the tool tracks the directory change for subsequent calls.

    Parameters
    ----------
    initial_cwd:
        Starting working directory. Defaults to ``Path.cwd()``.
    """
    state = {"cwd": (initial_cwd or Path.cwd()).resolve()}

    async def execute(
        tool_call_id: str,
        args: dict[str, Any],
        cancel: Any = None,
        on_update: Any = None,
        **kwargs: Any,
    ) -> AgentToolResult:
        command = args.get("command", "")
        if not command:
            return AgentToolResult(
                content=(TextContent(text="Error: no command provided"),),
            )

        timeout_ms = args.get("timeout_ms")
        if timeout_ms is not None:
            timeout_ms = max(1000, min(int(timeout_ms), _MAX_BASH_TIMEOUT_MS))
        else:
            timeout_ms = _DEFAULT_BASH_TIMEOUT_MS

        timeout_s = timeout_ms / 1000.0

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(state["cwd"]),
                start_new_session=True,
            )

            stdout_lines: list[str] = []
            stderr_lines: list[str] = []

            async def _read_stream(
                stream: asyncio.StreamReader,
                lines_out: list[str],
            ) -> None:
                """Read stream line-by-line, calling on_update with progress."""
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    lines_out.append(line.decode("utf-8", errors="replace"))
                    if on_update is not None:
                        on_update(AgentToolResult(
                            content=(TextContent(text="".join(lines_out)),),
                        ))

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _read_stream(proc.stdout, stdout_lines),
                        _read_stream(proc.stderr, stderr_lines),
                    ),
                    timeout=timeout_s,
                )
                await proc.wait()
            except asyncio.TimeoutError:
                kill_process_tree(proc)
                return AgentToolResult(
                    content=(TextContent(text=f"Command timed out after {timeout_ms}ms."),),
                )

            stdout = "".join(stdout_lines)
            stderr = "".join(stderr_lines)
            exit_code = proc.returncode or 0

            # Track CWD changes
            state["cwd"] = try_update_cwd(command, state["cwd"])

            parts: list[str] = []
            if stdout:
                parts.append(stdout.rstrip())
            if stderr:
                parts.append(f"[stderr]\n{stderr.rstrip()}")
            if exit_code != 0:
                parts.append(f"[exit code: {exit_code}]")

            output = "\n".join(parts) if parts else "(no output)"

            # Write large output to temp file
            if len(output) > _BASH_TEMP_THRESHOLD:
                temp_path = Path(tempfile.gettempdir()) / f"bash_output_{uuid.uuid4().hex[:8]}.txt"
                temp_path.write_text(output, encoding="utf-8")
                truncated = truncate(output)
                truncated += f"\n\n[Full output written to {temp_path}]"
                return AgentToolResult(
                    content=(TextContent(text=truncated),),
                    details={"full_output_path": str(temp_path)},
                )

            return AgentToolResult(content=(TextContent(text=output),))

        except Exception as exc:
            return AgentToolResult(
                content=(TextContent(text=f"Error: {exc}"),),
            )

    return AgentTool(
        name="bash",
        description=(
            "Execute a bash command in the shell. The working directory persists "
            "between calls. Commands time out after 2 minutes by default (max 10 min). "
            "Use for git, npm, docker, builds, and other terminal operations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": (
                        "Optional timeout in milliseconds (max 600000). "
                        "Defaults to 120000 (2 minutes)."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the command does.",
                },
            },
            "required": ["command"],
        },
        execute=execute,
    )
