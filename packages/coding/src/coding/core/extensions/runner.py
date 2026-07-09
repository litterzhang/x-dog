"""Extension runner: execute extension hooks at the right time."""

from __future__ import annotations

from typing import Any

from coding.core.extensions.types import (
    Extension,
    PostToolUseHook,
    PreToolUseHook,
    StopHook,
)


class ExtensionRunner:
    """Manages a set of loaded extensions and runs their hooks."""

    def __init__(self, extensions: list[Extension] | None = None) -> None:
        self._extensions = list(extensions or [])

    def add(self, extension: Extension) -> None:
        """Register an extension."""
        self._extensions.append(extension)

    @property
    def extensions(self) -> list[Extension]:
        return list(self._extensions)

    async def run_pre_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Run all PreToolUseHook hooks.

        Returns the (possibly modified) params, or ``None`` if any hook
        aborted the tool call.
        """
        current_params = dict(params)

        for ext in self._extensions:
            for hook in ext.get_hooks_of_type(PreToolUseHook):
                assert isinstance(hook, PreToolUseHook)
                result = await hook.before_tool(tool_name, current_params)
                if result is None:
                    return None
                current_params = result

        return current_params

    async def run_post_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: str,
    ) -> str:
        """Run all PostToolUseHook hooks.

        Returns the (possibly modified) result string.
        """
        current_result = result

        for ext in self._extensions:
            for hook in ext.get_hooks_of_type(PostToolUseHook):
                assert isinstance(hook, PostToolUseHook)
                current_result = await hook.after_tool(tool_name, params, current_result)

        return current_result

    async def run_stop(self) -> None:
        """Run all StopHook hooks."""
        for ext in self._extensions:
            for hook in ext.get_hooks_of_type(StopHook):
                assert isinstance(hook, StopHook)
                try:
                    await hook.on_stop()
                except Exception:
                    # Stop hooks should not prevent shutdown
                    pass
