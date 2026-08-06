"""Execution utilities: subprocess helpers and timeout management."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

from xdog.coding.core.defaults import DEFAULT_BASH_TIMEOUT_MS, MAX_BASH_TIMEOUT_MS


@dataclass(frozen=True)
class ExecResult:
    """Result of a subprocess execution."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Combined stdout+stderr (stdout preferred)."""
        if self.stdout and self.stderr:
            return self.stdout + "\n" + self.stderr
        return self.stdout or self.stderr


def clamp_timeout(timeout_ms: int | None) -> float:
    """Clamp and convert a timeout in milliseconds to seconds."""
    ms = timeout_ms if timeout_ms is not None else DEFAULT_BASH_TIMEOUT_MS
    ms = max(1000, min(ms, MAX_BASH_TIMEOUT_MS))
    return ms / 1000.0


def run_sync(
    command: str,
    *,
    cwd: str | Path | None = None,
    timeout_ms: int | None = None,
    env: dict[str, str] | None = None,
) -> ExecResult:
    """Run a shell command synchronously.

    Returns an :class:`ExecResult` with stdout, stderr, exit code, and
    a flag indicating whether the process timed out.
    """
    timeout_s = clamp_timeout(timeout_ms)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout_s,
            env=env,
        )
        return ExecResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired:
        return ExecResult(
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout_s:.0f}s",
            timed_out=True,
        )
    except OSError as exc:
        return ExecResult(
            exit_code=-1,
            stdout="",
            stderr=f"Failed to execute command: {exc}",
        )


async def run_async(
    command: str,
    *,
    cwd: str | Path | None = None,
    timeout_ms: int | None = None,
    env: dict[str, str] | None = None,
) -> ExecResult:
    """Run a shell command asynchronously.

    Uses ``asyncio.create_subprocess_shell`` for non-blocking execution.
    """
    timeout_s = clamp_timeout(timeout_ms)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {timeout_s:.0f}s",
                timed_out=True,
            )
        return ExecResult(
            exit_code=proc.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )
    except OSError as exc:
        return ExecResult(
            exit_code=-1,
            stdout="",
            stderr=f"Failed to execute command: {exc}",
        )
