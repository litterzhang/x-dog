"""Theme definitions for the interactive coding agent TUI.

Provides color palettes, text styling functions, and markdown theme
configuration matching the coding's dark terminal aesthetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tui.components.markdown import DefaultTextStyle, MarkdownTheme

_RST = "\x1b[0m"


def _fg(hex_color: str) -> Callable[[str], str]:
    """Return a function that applies foreground color (fg-only reset)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    prefix = f"\x1b[38;2;{r};{g};{b}m"

    def apply(text: str) -> str:
        return f"{prefix}{text}\x1b[39m"

    return apply


def _bg(hex_color: str) -> Callable[[str], str]:
    """Return a function that applies background color (bg-only reset)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    prefix = f"\x1b[48;2;{r};{g};{b}m"

    def apply(text: str) -> str:
        return f"{prefix}{text}\x1b[49m"

    return apply


def _bold(text: str) -> str:
    return f"\x1b[1m{text}{_RST}"


def _dim(text: str) -> str:
    return f"\x1b[2m{text}{_RST}"


def _italic(text: str) -> str:
    return f"\x1b[3m{text}{_RST}"


def _inverse(text: str) -> str:
    return f"\x1b[7m{text}\x1b[27m"


# -- Dark palette --

PALETTE = {
    "text": "#E8E3D5",
    "dim": "#7B7F87",
    "accent": "#F6C453",
    "accent_soft": "#F2A65A",
    "border": "#3C414B",
    "user_bg": "#2B2F36",
    "user_text": "#F3EEE0",
    "system_text": "#9BA3B2",
    "quote": "#8CC8FF",
    "quote_border": "#3B4D6B",
    "code": "#F0C987",
    "code_block": "#1E232A",
    "code_border": "#343A45",
    "link": "#7DD3A5",
    "error": "#F97066",
    "success": "#7DD3A5",
    "tool": "#A0C4FF",
    "diff_added": "#b5bd68",
    "diff_removed": "#cc6666",
    "diff_context": "#808080",
}


@dataclass(frozen=True)
class Theme:
    """Resolved theme with callable styling functions."""

    fg: Callable[[str], str]
    dim: Callable[[str], str]
    accent: Callable[[str], str]
    accent_soft: Callable[[str], str]
    border: Callable[[str], str]
    user_bg: Callable[[str], str]
    user_text: Callable[[str], str]
    system: Callable[[str], str]
    error: Callable[[str], str]
    success: Callable[[str], str]
    tool: Callable[[str], str]
    bold: Callable[[str], str]
    italic: Callable[[str], str]
    header: Callable[[str], str]
    diff_added: Callable[[str], str]
    diff_removed: Callable[[str], str]
    diff_context: Callable[[str], str]
    inverse: Callable[[str], str]
    markdown: MarkdownTheme
    user_default_text: DefaultTextStyle


def create_default_theme() -> Theme:
    """Create the default dark theme."""
    fg = _fg(PALETTE["text"])
    dim_fn = _fg(PALETTE["dim"])
    accent = _fg(PALETTE["accent"])
    accent_soft = _fg(PALETTE["accent_soft"])
    border = _fg(PALETTE["border"])
    user_bg = _bg(PALETTE["user_bg"])
    user_text = _fg(PALETTE["user_text"])
    system = _fg(PALETTE["system_text"])
    error = _fg(PALETTE["error"])
    success = _fg(PALETTE["success"])
    tool = _fg(PALETTE["tool"])

    md_theme = MarkdownTheme(
        heading=lambda t: _bold(_fg(PALETTE["accent"])(t)),
        link=_fg(PALETTE["link"]),
        link_url=lambda t: _dim(t),
        code=_fg(PALETTE["code"]),
        code_block=_fg(PALETTE["code"]),
        code_block_border=_fg(PALETTE["code_border"]),
        quote=_fg(PALETTE["quote"]),
        quote_border=_fg(PALETTE["quote_border"]),
        hr=border,
        list_bullet=_fg(PALETTE["accent_soft"]),
        bold=_bold,
        italic=_italic,
    )

    user_default = DefaultTextStyle(
        color=user_text,
        bg_color=user_bg,
    )

    return Theme(
        fg=fg,
        dim=dim_fn,
        accent=accent,
        accent_soft=accent_soft,
        border=border,
        user_bg=user_bg,
        user_text=user_text,
        system=system,
        error=error,
        success=success,
        tool=tool,
        bold=_bold,
        italic=_italic,
        header=lambda t: _bold(accent(t)),
        diff_added=_fg(PALETTE["diff_added"]),
        diff_removed=_fg(PALETTE["diff_removed"]),
        diff_context=_fg(PALETTE["diff_context"]),
        inverse=_inverse,
        markdown=md_theme,
        user_default_text=user_default,
    )


def format_tokens(count: int) -> str:
    """Format token count for display.

    < 1,000:       raw number (e.g. "500")
    < 10,000:      one decimal (e.g. "5.2k")
    < 1,000,000:   rounded thousands (e.g. "150k")
    >= 1,000,000:  one decimal millions (e.g. "5.2M")
    """
    if count < 1_000:
        return str(count)
    if count < 10_000:
        return f"{count / 1_000:.1f}k"
    if count < 1_000_000:
        return f"{count // 1_000}k"
    if count < 10_000_000:
        return f"{count / 1_000_000:.1f}M"
    return f"{count // 1_000_000}M"
