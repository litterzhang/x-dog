---
title: Design
---

How tui paints a terminal UI without flicker or an alternate screen — the ideas
behind the differential renderer and the string-returning component model.

## Differential line rendering, in the main buffer

Each component returns its view as a list of strings — `Component.render(width)` →
`list[str]`. The engine (`tui.py`) diffs a frame against the previous one and
emits only the lines that changed, using relative cursor moves wrapped in
synchronized-output escapes so a repaint never tears.

It renders in the terminal's main buffer, not the alternate screen — so
scrollback is preserved and the app's output stays in your history like any other
command.

## Components compose by concatenation

A `Container` stacks its children vertically by concatenating their rendered
lines; `TUI` is itself a `Container`. A `Component` adds `handle_input` and
`invalidate`. There is no retained widget tree to reconcile — the model is
"functions that return strings."

## Overlays with focus capture

The overlay system composites a floating panel over the base view: anchored,
sized by integer or percentage, with margins and offsets. An overlay can capture
focus, which is how modals, pickers, and autocomplete popups work.

## Real key parsing

`keys.py` parses VT100/xterm sequences and the Kitty keyboard protocol (CSI-u),
distinguishing press, repeat, and release events and decoding modifiers, function
keys, and backtab. A `KeybindingManager` maps those events to actions by scope.

## Inline images without Pillow

`terminal_image.py` speaks the Kitty Graphics and iTerm2 inline-image protocols,
detecting terminal capability from the environment. Image dimensions are read by
pure-Python PNG / JPEG / GIF / WebP parsers, so there is no native-library
dependency.
