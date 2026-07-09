"""Tool execution component for the interactive TUI."""

from __future__ import annotations

import re

from tui.tui import Container, Component
from tui.components.text import Text
from tui.components.diff import Diff

from coding.modes.interactive.theme import Theme

# Threshold for collapsing large non-diff output
_COLLAPSE_THRESHOLD = 500


class ToolExecutionComponent(Container):
    """Renders a tool call with its name, parameters, and result.

    Tracks execution state (pending → running → success/error) and renders
    appropriate visual indicators. For edit operations that produce diffs,
    the result is rendered using the Diff component with colored lines and
    intra-line highlighting. Large outputs are collapsed with a summary.
    """

    def __init__(
        self,
        tool_name: str,
        arguments: dict | None,
        theme: Theme,
    ) -> None:
        super().__init__()
        self._theme = theme
        self._tool_name = tool_name
        self._state = "running"  # running → success | error
        self._result_text: Text | None = None
        self._diff_component: Diff | None = None
        self._header_text: Text | None = None

        # Tool call header with state icon
        header_str = self._make_header()
        self._header_text = Text(header_str, 0, 0)
        self.add_child(self._header_text)

        # Show arguments if present
        if arguments:
            summary = _summarize_args(arguments, tool_name)
            if summary:
                self.add_child(Text(theme.dim(f"    {summary}"), 0, 0))

    def _make_header(self) -> str:
        """Build header string with state-appropriate icon and color."""
        icons = {"running": "⚡", "success": "✓", "error": "✗"}
        icon = icons.get(self._state, "⚡")
        color_fns = {
            "running": self._theme.tool,
            "success": self._theme.success,
            "error": self._theme.error,
        }
        color_fn = color_fns.get(self._state, self._theme.tool)
        return color_fn(f"  {icon} {self._tool_name}")

    def _update_header(self) -> None:
        """Refresh the header text after a state change."""
        if self._header_text is not None:
            self._header_text.set_text(self._make_header())

    def set_streaming(self, text: str) -> None:
        """Show live streaming output preview (e.g. bash stdout)."""
        # Show last 10 lines of streaming output
        lines = text.strip().split("\n")
        preview = "\n".join(lines[-10:]) if len(lines) > 10 else text.strip()
        display = _truncate(preview, 500)
        if self._result_text is not None:
            self._result_text.set_text(self._theme.dim(f"    ⏳ {display}"))
        else:
            self._result_text = Text(
                self._theme.dim(f"    ⏳ {display}"),
                0, 0,
            )
            self.add_child(self._result_text)

    def set_result(self, result: str, *, is_error: bool = False) -> None:
        """Set the tool result text.

        If the result contains a custom diff (lines prefixed with ``+linenum``
        or ``-linenum``), it is rendered using the Diff component with colored
        lines and intra-line highlighting. Otherwise, a truncated plain text
        summary is shown. Large outputs (>500 chars) are collapsed.
        """
        # Update state
        self._state = "error" if is_error else "success"
        self._update_header()

        if _contains_diff(result):
            # Extract the diff portion (after the summary line)
            diff_text = _extract_diff(result)
            summary = _extract_summary(result)

            # Show the summary line
            if summary and self._result_text is None:
                self._result_text = Text(
                    self._theme.dim(f"    → {summary}"),
                    0, 0,
                )
                self.add_child(self._result_text)

            # Render the diff with colors and intra-line highlighting
            if self._diff_component is not None:
                self._diff_component.set_diff(diff_text)
            else:
                self._diff_component = Diff(
                    diff_text,
                    padding_left=4,
                    color_added=self._theme.diff_added,
                    color_removed=self._theme.diff_removed,
                    color_context=self._theme.diff_context,
                    inverse=self._theme.inverse,
                )
                self.add_child(self._diff_component)
        else:
            # Plain text result — collapse large outputs
            if len(result) > _COLLAPSE_THRESHOLD:
                display = result[:200].replace("\n", " ").replace("\r", "")
                line_count = result.count("\n")
                display += f" [...{len(result) - 200} more chars, {line_count} lines]"
            else:
                display = _truncate(result, 200)

            color_fn = self._theme.error if is_error else self._theme.dim
            if self._result_text is not None:
                self._result_text.set_text(color_fn(f"    → {display}"))
            else:
                self._result_text = Text(
                    color_fn(f"    → {display}"),
                    0, 0,
                )
                self.add_child(self._result_text)


_DIFF_LINE_RE = re.compile(r"^[+\-]\s*\d+\s", re.MULTILINE)


def _contains_diff(text: str) -> bool:
    """Check if the text contains a custom diff (``+linenum`` / ``-linenum`` format)."""
    return _DIFF_LINE_RE.search(text) is not None


def _extract_diff(text: str) -> str:
    """Extract the custom diff portion from the result text.

    The result typically starts with a summary line like
    "Successfully replaced 1 occurrence(s) in /path/to/file"
    followed by a blank line and the diff (lines starting with +/-/space + linenum).
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _DIFF_LINE_RE.match(line) or line == "...":
            return "\n".join(lines[i:])
    return text


def _extract_summary(text: str) -> str:
    """Extract the summary line before the diff."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _DIFF_LINE_RE.match(line) or line == "...":
            summary_lines = lines[:i]
            return " ".join(ln.strip() for ln in summary_lines if ln.strip())
    return ""


def _summarize_args(args: dict, tool_name: str = "") -> str:
    """Create a tool-aware compact summary of arguments.

    Uses specialized formatting for known tools (bash, filesystem, grep, find)
    and falls back to generic key=value pairs for unknown tools.
    """
    if tool_name == "bash":
        cmd = args.get("command", "")
        return cmd if len(cmd) <= 80 else cmd[:77] + "..."
    if tool_name == "filesystem":
        action = args.get("action", "")
        path = args.get("path", "")
        return f"{action} {path}"
    if tool_name == "grep":
        pattern = args.get("pattern", "")
        path = args.get("path", ".")
        return f"/{pattern}/ in {path}"
    if tool_name == "find":
        pattern = args.get("pattern", "")
        path = args.get("path", ".")
        return f"{pattern} in {path}"
    return _generic_summarize_args(args)


def _generic_summarize_args(args: dict) -> str:
    """Generic key=value argument summary."""
    parts: list[str] = []
    for key, value in args.items():
        if isinstance(value, str):
            display = value if len(value) <= 60 else value[:57] + "..."
            parts.append(f"{key}={display!r}")
        elif isinstance(value, (int, float, bool)):
            parts.append(f"{key}={value}")
        else:
            parts.append(f"{key}=...")
    result = ", ".join(parts)
    return result[:200] if len(result) > 200 else result


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, replacing newlines with spaces."""
    text = text.replace("\n", " ").replace("\r", "")
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
