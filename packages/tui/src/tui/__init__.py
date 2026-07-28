"""tui -- Terminal UI library with differential rendering.

This package provides a complete terminal UI framework including:

- **Terminal**: Raw terminal I/O with differential (incremental) rendering.
- **TUI / TuiEngine**: Component lifecycle management, rendering pipeline,
  input dispatch, overlay system.
- **Components**: Ready-made UI components (Box, Text, Input, Editor, Markdown,
  Image, SettingsList, CancellableLoader, etc.).
- **Keys**: Terminal key-event parsing from raw stdin bytes (VT100, xterm,
  Kitty keyboard protocol).
- **Keybindings**: Declarative keybinding registration and matching.
- **Fuzzy**: Fuzzy string matching and filtering.
- **Autocomplete**: Pluggable autocomplete engine with filesystem support.
- **Terminal Image**: Kitty/iTerm2 inline image rendering.
"""

from tui.autocomplete import (
    AutocompleteEngine,
    AutocompleteResult,
    CallbackCompletionProvider,
    CombinedCompletionProvider,
    CompletionItem,
    CompletionProvider,
    FileSystemCompletionProvider,
    SlashCommand,
    SlashCommandProvider,
    StaticCompletionProvider,
)
from tui.editor_component import EditorComponent
from tui.fuzzy import FuzzyMatch, fuzzy_filter, fuzzy_match
from tui.keybindings import (
    Keybinding,
    KeybindingManager,
    KeybindingScope,
)
from tui.keys import (
    KeyEvent,
    KeyEventType,
    SpecialKey,
    is_key_release,
    is_key_repeat,
    is_kitty_protocol_active,
    parse_key_events,
    set_kitty_protocol_active,
)
from tui.kill_ring import KillRing
from tui.terminal import Cell, Color, ScreenBuffer, Style, Terminal
from tui.terminal_image import (
    CellDimensions,
    ImageDimensions,
    ImageRenderOptions,
    ImageRenderResult,
    TerminalCapabilities,
    detect_capabilities,
    get_capabilities,
    render_image,
    reset_capabilities_cache,
)
from tui.tui import (
    CURSOR_MARKER,
    TUI,
    Component,
    Container,
    Focusable,
    OverlayAnchor,
    OverlayHandle,
    OverlayMargin,
    OverlayOptions,
    SizeValue,
    is_focusable,
)
from tui.undo_stack import UndoStack
from tui.utils import (
    Segment,
    apply_background_to_line,
    char_width,
    clamp,
    extract_segments,
    pad_center,
    pad_right,
    slice_by_column,
    slice_with_width,
    string_width,
    strip_ansi,
    truncate_to_width,
    visible_width,
    wrap_text,
    wrap_text_with_ansi,
)

__version__ = "0.57.1"

__all__ = [
    # terminal
    "Cell",
    "Color",
    "ScreenBuffer",
    "Style",
    "Terminal",
    # tui engine
    "Component",
    "Container",
    "CURSOR_MARKER",
    "Focusable",
    "is_focusable",
    "OverlayAnchor",
    "OverlayHandle",
    "OverlayMargin",
    "OverlayOptions",
    "SizeValue",
    "TUI",
    # keys
    "KeyEvent",
    "KeyEventType",
    "SpecialKey",
    "is_key_release",
    "is_key_repeat",
    "is_kitty_protocol_active",
    "parse_key_events",
    "set_kitty_protocol_active",
    # keybindings
    "Keybinding",
    "KeybindingManager",
    "KeybindingScope",
    # fuzzy
    "FuzzyMatch",
    "fuzzy_filter",
    "fuzzy_match",
    # autocomplete
    "AutocompleteEngine",
    "AutocompleteResult",
    "CallbackCompletionProvider",
    "CombinedCompletionProvider",
    "CompletionItem",
    "CompletionProvider",
    "FileSystemCompletionProvider",
    "SlashCommand",
    "SlashCommandProvider",
    "StaticCompletionProvider",
    # editor component protocol
    "EditorComponent",
    # undo
    "UndoStack",
    # kill ring
    "KillRing",
    # terminal image
    "CellDimensions",
    "ImageDimensions",
    "ImageRenderOptions",
    "ImageRenderResult",
    "TerminalCapabilities",
    "detect_capabilities",
    "get_capabilities",
    "render_image",
    "reset_capabilities_cache",
    # utils
    "Segment",
    "apply_background_to_line",
    "char_width",
    "clamp",
    "extract_segments",
    "pad_center",
    "pad_right",
    "slice_by_column",
    "slice_with_width",
    "string_width",
    "strip_ansi",
    "truncate_to_width",
    "visible_width",
    "wrap_text",
    "wrap_text_with_ansi",
]
