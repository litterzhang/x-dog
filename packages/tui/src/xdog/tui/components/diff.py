"""Diff component: renders custom diff format with intra-line highlighting.

Matches the original TypeScript ``renderDiff()`` implementation:

- Parses lines in ``+linenum content`` / ``-linenum content`` / `` linenum content`` format
- Applies theme colors (green for additions, red for deletions, gray for context)
- When exactly one removed + one added line appear together, computes **word-level
  diff** and highlights changed tokens with inverse styling
"""

from __future__ import annotations

import difflib
import re
from typing import Callable

from xdog.tui.tui import Component
from xdog.tui.utils import wrap_text_with_ansi

# Default ANSI codes used when no theme is provided
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_DIM = "\x1b[2m"
_INVERSE = "\x1b[7m"
_INVERSE_OFF = "\x1b[27m"
_RESET = "\x1b[0m"

# Pattern: prefix (+/-/space), optional line number, space, content
_DIFF_LINE_RE = re.compile(r"^([+\-\s])(\s*\d*)\s(.*)$")


class Diff(Component):
    """Renders a custom-format diff with colored lines and intra-line highlighting.

    Lines are colored based on their prefix:

    - ``+`` lines (additions) are green, with changed tokens in inverse
    - ``-`` lines (deletions) are red, with changed tokens in inverse
    - Context lines are dimmed/gray

    When exactly one removed line is followed by one added line, the component
    computes a **word-level diff** and highlights only the changed tokens using
    inverse styling — matching the original TypeScript implementation.
    """

    def __init__(
        self,
        diff_text: str,
        padding_left: int = 2,
        padding_top: int = 0,
        padding_bottom: int = 0,
        *,
        color_added: Callable[[str], str] | None = None,
        color_removed: Callable[[str], str] | None = None,
        color_context: Callable[[str], str] | None = None,
        inverse: Callable[[str], str] | None = None,
    ) -> None:
        self._diff_text = diff_text
        self._padding_left = padding_left
        self._padding_top = padding_top
        self._padding_bottom = padding_bottom
        self._color_added = color_added or (lambda t: f"{_GREEN}{t}{_RESET}")
        self._color_removed = color_removed or (lambda t: f"{_RED}{t}{_RESET}")
        self._color_context = color_context or (lambda t: f"{_DIM}{t}{_RESET}")
        self._inverse = inverse or (lambda t: f"{_INVERSE}{t}{_INVERSE_OFF}")

    def set_diff(self, diff_text: str) -> None:
        """Update the diff text."""
        self._diff_text = diff_text

    def render(self, width: int) -> list[str]:
        """Render the diff with ANSI coloring and intra-line highlighting."""
        if not self._diff_text:
            return []

        content_width = max(1, width - self._padding_left)
        pad = " " * self._padding_left
        output: list[str] = []

        # Top padding
        for _ in range(self._padding_top):
            output.append("")

        lines = self._diff_text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            parsed = _parse_diff_line(line)

            if parsed is None:
                # Unparseable line (e.g. "..." ellipsis) → context color
                styled = self._color_context(line)
                for wl in wrap_text_with_ansi(styled, content_width):
                    output.append(pad + wl)
                i += 1
                continue

            prefix, line_num, content = parsed

            if prefix == "-":
                # Collect consecutive removed lines
                removed_lines: list[tuple[str, str]] = []
                while i < len(lines):
                    p = _parse_diff_line(lines[i])
                    if p is None or p[0] != "-":
                        break
                    removed_lines.append((p[1], p[2]))
                    i += 1

                # Collect consecutive added lines
                added_lines: list[tuple[str, str]] = []
                while i < len(lines):
                    p = _parse_diff_line(lines[i])
                    if p is None or p[0] != "+":
                        break
                    added_lines.append((p[1], p[2]))
                    i += 1

                # Intra-line diffing when exactly 1 removed + 1 added
                if len(removed_lines) == 1 and len(added_lines) == 1:
                    rem_num, rem_content = removed_lines[0]
                    add_num, add_content = added_lines[0]

                    rem_rendered, add_rendered = _render_intra_line_diff(
                        _replace_tabs(rem_content),
                        _replace_tabs(add_content),
                        self._inverse,
                    )

                    styled_rem = self._color_removed(f"-{rem_num} {rem_rendered}")
                    styled_add = self._color_added(f"+{add_num} {add_rendered}")
                    for wl in wrap_text_with_ansi(styled_rem, content_width):
                        output.append(pad + wl)
                    for wl in wrap_text_with_ansi(styled_add, content_width):
                        output.append(pad + wl)
                else:
                    # Show all removed, then all added
                    for rnum, rcontent in removed_lines:
                        styled = self._color_removed(f"-{rnum} {_replace_tabs(rcontent)}")
                        for wl in wrap_text_with_ansi(styled, content_width):
                            output.append(pad + wl)
                    for anum, acontent in added_lines:
                        styled = self._color_added(f"+{anum} {_replace_tabs(acontent)}")
                        for wl in wrap_text_with_ansi(styled, content_width):
                            output.append(pad + wl)

            elif prefix == "+":
                # Standalone added line
                styled = self._color_added(f"+{line_num} {_replace_tabs(content)}")
                for wl in wrap_text_with_ansi(styled, content_width):
                    output.append(pad + wl)
                i += 1

            else:
                # Context line
                styled = self._color_context(f" {line_num} {_replace_tabs(content)}")
                for wl in wrap_text_with_ansi(styled, content_width):
                    output.append(pad + wl)
                i += 1

        # Bottom padding
        for _ in range(self._padding_bottom):
            output.append("")

        return output


def _parse_diff_line(line: str) -> tuple[str, str, str] | None:
    """Parse a diff line into ``(prefix, line_num, content)`` or ``None``."""
    m = _DIFF_LINE_RE.match(line)
    if m is None:
        return None
    return (m.group(1), m.group(2), m.group(3))


def _replace_tabs(text: str) -> str:
    """Replace tabs with spaces for consistent rendering."""
    return text.replace("\t", "   ")


def _render_intra_line_diff(
    old_content: str,
    new_content: str,
    inverse_fn: Callable[[str], str],
) -> tuple[str, str]:
    """Compute word-level diff and render with inverse on changed parts.

    Matches the original TS ``renderIntraLineDiff()``: uses word-level diffing
    and strips leading whitespace from inverse to avoid highlighting indentation.

    Returns ``(removed_line, added_line)`` with inverse-styled changed tokens.
    """
    # Split into words (keeping whitespace as separate tokens for precision)
    old_words = _tokenize_for_diff(old_content)
    new_words = _tokenize_for_diff(new_content)

    sm = difflib.SequenceMatcher(None, old_words, new_words)

    removed_line = ""
    added_line = ""
    is_first_removed = True
    is_first_added = True

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            chunk = "".join(old_words[i1:i2])
            removed_line += chunk
            added_line += chunk
        elif tag == "replace":
            old_chunk = "".join(old_words[i1:i2])
            new_chunk = "".join(new_words[j1:j2])
            # Strip leading whitespace from inverse on first occurrence
            if is_first_removed:
                leading = len(old_chunk) - len(old_chunk.lstrip())
                if leading > 0:
                    removed_line += old_chunk[:leading]
                    old_chunk = old_chunk[leading:]
                is_first_removed = False
            if is_first_added:
                leading = len(new_chunk) - len(new_chunk.lstrip())
                if leading > 0:
                    added_line += new_chunk[:leading]
                    new_chunk = new_chunk[leading:]
                is_first_added = False
            if old_chunk:
                removed_line += inverse_fn(old_chunk)
            if new_chunk:
                added_line += inverse_fn(new_chunk)
        elif tag == "delete":
            old_chunk = "".join(old_words[i1:i2])
            if is_first_removed:
                leading = len(old_chunk) - len(old_chunk.lstrip())
                if leading > 0:
                    removed_line += old_chunk[:leading]
                    old_chunk = old_chunk[leading:]
                is_first_removed = False
            if old_chunk:
                removed_line += inverse_fn(old_chunk)
        elif tag == "insert":
            new_chunk = "".join(new_words[j1:j2])
            if is_first_added:
                leading = len(new_chunk) - len(new_chunk.lstrip())
                if leading > 0:
                    added_line += new_chunk[:leading]
                    new_chunk = new_chunk[leading:]
                is_first_added = False
            if new_chunk:
                added_line += inverse_fn(new_chunk)

    return (removed_line, added_line)


def _tokenize_for_diff(text: str) -> list[str]:
    """Tokenize text into words and whitespace runs for word-level diffing.

    Groups whitespace with adjacent words (similar to the JS ``diffWords``
    behavior) for cleaner highlighting.
    """
    tokens: list[str] = []
    current = ""
    for ch in text:
        if ch in (" ", "\t"):
            current += ch
        else:
            if current and tokens:
                # Attach whitespace to the next word
                current += ch
                continue
            elif current:
                current += ch
                continue
            else:
                if current:
                    tokens.append(current)
                current = ch
                continue
        # If we get here, ch is whitespace
        if current and not current.isspace():
            # We have a word followed by whitespace — continue building
            current += ch
        else:
            current += ch

    if current:
        tokens.append(current)

    return tokens if tokens else [text]
