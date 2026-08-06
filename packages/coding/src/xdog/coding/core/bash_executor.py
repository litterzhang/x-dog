"""Bash command executor with working-directory tracking."""

from __future__ import annotations

from pathlib import Path

from xdog.coding.core.exec_utils import ExecResult, run_async, run_sync


class BashExecutor:
    """Stateful bash executor that tracks the current working directory.

    Each command runs in the tracked cwd.  If a command contains ``cd``,
    the executor tries to update the tracked directory accordingly.
    """

    def __init__(self, initial_cwd: str | Path | None = None) -> None:
        self._cwd = Path(initial_cwd).resolve() if initial_cwd else Path.cwd()

    @property
    def cwd(self) -> Path:
        return self._cwd

    def set_cwd(self, path: str | Path) -> None:
        resolved = Path(path).resolve()
        if resolved.is_dir():
            self._cwd = resolved

    def execute(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
    ) -> ExecResult:
        """Execute *command* synchronously in the current working directory."""
        result = run_sync(command, cwd=self._cwd, timeout_ms=timeout_ms)
        self._try_update_cwd(command)
        return result

    async def execute_async(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
    ) -> ExecResult:
        """Execute *command* asynchronously in the current working directory."""
        result = await run_async(command, cwd=self._cwd, timeout_ms=timeout_ms)
        self._try_update_cwd(command)
        return result

    def _try_update_cwd(self, command: str) -> None:
        """Attempt to track ``cd`` commands to keep cwd accurate.

        This is best-effort; complex shell scripts with conditional cd
        statements won't be tracked.
        """
        stripped = command.strip()
        # Handle simple `cd <path>` or `cd <path> && ...`
        if stripped.startswith("cd "):
            parts = stripped[3:].split("&&", 1)
            target = parts[0].strip().strip('"').strip("'")
            if target:
                candidate = (self._cwd / target).resolve()
                if candidate.is_dir():
                    self._cwd = candidate
