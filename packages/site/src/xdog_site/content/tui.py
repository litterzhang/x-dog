"""Deep-dive content for the ``tui`` package — a terminal UI library.

Accurate against packages/tui/src/tui: the differential line renderer, the
component/container model, overlays, key parsing (xterm + Kitty), inline images
(Kitty / iTerm2), and the shipped components. It is a library — no CLI.
"""

from __future__ import annotations

from xdog_site.content.docs import Feature, PackageDocs, Phase, RefBlock, Section

_DESIGN = (
    Section(
        heading="Differential line rendering, in the main buffer",
        body=(
            "Each component returns its view as a list of strings — Component.render(width) → "
            "list[str]. The engine (tui.py) diffs a frame against the previous one and emits only the "
            "lines that changed, using relative cursor moves wrapped in synchronized-output escapes "
            "so a repaint never tears.",
            "It renders in the terminal's main buffer, not the alternate screen — so scrollback is "
            "preserved and the app's output stays in your history like any other command.",
        ),
    ),
    Section(
        heading="Components compose by concatenation",
        body=(
            "A Container stacks its children vertically by concatenating their rendered lines; TUI is "
            "itself a Container. A Component adds handle_input and invalidate. There is no retained "
            "widget tree to reconcile — the model is \"functions that return strings.\"",
        ),
    ),
    Section(
        heading="Overlays with focus capture",
        body=(
            "The overlay system composites a floating panel over the base view: anchored, sized by "
            "integer or percentage, with margins and offsets. An overlay can capture focus, which is "
            "how modals, pickers, and autocomplete popups work.",
        ),
    ),
    Section(
        heading="Real key parsing",
        body=(
            "keys.py parses VT100/xterm sequences and the Kitty keyboard protocol (CSI-u), "
            "distinguishing press, repeat, and release events and decoding modifiers, function keys, "
            "and backtab. A KeybindingManager maps those events to actions by scope.",
        ),
    ),
    Section(
        heading="Inline images without Pillow",
        body=(
            "terminal_image.py speaks the Kitty Graphics and iTerm2 inline-image protocols, detecting "
            "terminal capability from the environment. Image dimensions are read by pure-Python "
            "PNG / JPEG / GIF / WebP parsers, so there is no native-library dependency.",
        ),
    ),
)

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

_REFERENCE = (
    RefBlock(
        heading="Top-level API",
        body=("Exported from tui/__init__.py.",),
        columns=("Name", "Kind", "Purpose"),
        rows=(
            ("TUI / Container / Component", "class", "The engine and the component/container model."),
            ("Terminal / ScreenBuffer / Cell", "class", "Low-level terminal I/O and the screen grid."),
            ("Focusable / OverlayHandle", "class", "Focus participation and overlay control."),
            ("parse_key_events / KeyEvent", "api", "Key decoder and its event type."),
            ("KeybindingManager / Keybinding", "class", "Scoped key-to-action mapping."),
            ("fuzzy_match / fuzzy_filter", "function", "Fuzzy matching for pickers."),
            ("AutocompleteEngine", "class", "Completion with pluggable providers."),
            ("render_image / detect_capabilities", "function", "Inline image output + capability probe."),
            ("EditorComponent / UndoStack / KillRing", "class", "Editing primitives."),
        ),
    ),
    RefBlock(
        heading="Shipped components",
        body=("Ready-to-use components in tui/components/.",),
        columns=("Component", "Purpose"),
        rows=(
            ("Box · Text · Spacer", "Layout and static text."),
            ("Input · Editor", "Single-line and multi-line editing (undo, kill-ring)."),
            ("Markdown · Diff", "Rendered markdown and unified diffs."),
            ("Image", "Inline images via Kitty / iTerm2."),
            ("SelectList · SettingsList", "Menus and configuration UIs."),
            ("Loader · CancellableLoader", "Spinners with optional cancellation."),
        ),
    ),
    RefBlock(
        heading="Image protocols",
        body=("Chosen by capability detection from the environment.",),
        columns=("Protocol", "Notes"),
        rows=(
            ("Kitty Graphics", "Pixel-accurate inline images on Kitty-class terminals."),
            ("iTerm2 inline", "Inline images on iTerm2 and compatibles."),
            ("Dimension parsers", "Pure-Python PNG / JPEG / GIF / WebP — no Pillow."),
        ),
    ),
)

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
    design_intro="How tui paints a terminal UI without flicker or an alternate screen — the ideas "
                 "behind the differential renderer and the string-returning component model.",
    design_sections=_DESIGN,
    features_intro="What tui provides today. It is a library with one dependency (wcwidth); every "
                   "capability below is importable from the package.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    reference_intro="The top-level API, the shipped components, and the inline-image protocols.",
    reference_blocks=_REFERENCE,
    roadmap_intro="Shipped foundations plus where tui is heading in 2026. Planned items are "
                  "aspirational, not yet implemented.",
    roadmap=_ROADMAP,
)
