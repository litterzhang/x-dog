"""Dynamic (Features + Roadmap) content for the ``tui`` package.

tui's static pages (Overview / Design / Reference) are markdown under
``content/pages/tui/``; its Features and Roadmap stay in Python here. Accurate
against packages/tui/src/tui: the differential line renderer, the
component/container model, overlays, key parsing (xterm + Kitty), inline images
(Kitty / iTerm2), and the shipped components. It is a library — no CLI.
"""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

_FEATURES = (
    Feature("Differential renderer", "Diffs frames and repaints only changed lines with synchronized "
            "output — no flicker.", "Engine"),
    Feature("Main-buffer rendering", "No alternate screen; scrollback and history are preserved.",
            "Engine"),
    Feature("Overlays", "Anchored, sized, margined floating panels with focus capture.", "Engine"),
    Feature("Frame-paced loop", "~30 fps loop with resize detection and tick callbacks.", "Engine"),
    Feature("Hardware cursor placement", "A CURSOR_MARKER APC sequence positions the real terminal "
            "cursor for focused inputs.", "Engine"),
    Feature("Key parsing", "xterm + Kitty keyboard protocol, press/repeat/release, modifiers, "
            "function keys, backtab.", "Input"),
    Feature("Keybindings", "Scoped KeybindingManager mapping key events to named actions.", "Input"),
    Feature("Fuzzy match", "fuzzy_match / fuzzy_filter for pickers and command palettes.", "Input"),
    Feature("Autocomplete", "AutocompleteEngine with filesystem and slash-command providers.",
            "Input"),
    Feature("Editor component", "Multi-line editor with undo/redo (UndoStack) and an emacs kill-ring.",
            "Components"),
    Feature("Markdown & Diff", "Markdown rendering and a unified-diff component for tool output.",
            "Components"),
    Feature("Inline images", "Kitty / iTerm2 protocols with pure-Python PNG/JPEG/GIF/WebP sizing.",
            "Components"),
    Feature("Select & settings lists", "SelectList and SettingsList for menus and configuration UIs.",
            "Components"),
    Feature("Wide-character aware", "wcwidth-based width handling for CJK and emoji.", "Components"),
)

_FEATURE_CATEGORIES = ("Engine", "Input", "Components")

_ROADMAP = (
    Phase("Shipped", "Rendering engine", (
        "Differential line renderer with synchronized output",
        "Main-buffer rendering (scrollback preserved)",
        "Overlay system with focus capture",
        "Frame-paced loop, resize detection, tick callbacks",
    ), done=True),
    Phase("Shipped", "Input & components", (
        "xterm + Kitty key parsing, scoped keybindings",
        "Fuzzy match, autocomplete engine",
        "Editor (undo / kill-ring), Markdown, Diff, images, lists",
        "Pure-Python image sizing, wcwidth width handling",
    ), done=True),
    Phase("2026", "Richer widgets", (
        "Tables and scrollable viewports as first-class components",
        "Mouse events and hit-testing",
        "Theming tokens shared across components",
    )),
    Phase("2026", "Reach", (
        "Sixel image protocol for broader terminal support",
        "Layout helpers beyond vertical stacking",
        "Accessibility / screen-reader hints",
    )),
)

DOCS = PackageDocs(
    name="tui",
    features_intro="What tui provides today. It is a library with one dependency (wcwidth); every "
                   "capability below is importable from the package.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    roadmap_intro="Shipped foundations plus where tui is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)
