---
title: Reference
---

The top-level API, the shipped components, and the inline-image protocols.

## Top-level API

Exported from `tui/__init__.py`.

| Name | Kind | Purpose |
|---|---|---|
| `TUI / Container / Component` | class | The engine and the component/container model. |
| `Terminal / ScreenBuffer / Cell` | class | Low-level terminal I/O and the screen grid. |
| `Focusable / OverlayHandle` | class | Focus participation and overlay control. |
| `parse_key_events / KeyEvent` | api | Key decoder and its event type. |
| `KeybindingManager / Keybinding` | class | Scoped key-to-action mapping. |
| `fuzzy_match / fuzzy_filter` | function | Fuzzy matching for pickers. |
| `AutocompleteEngine` | class | Completion with pluggable providers. |
| `render_image / detect_capabilities` | function | Inline image output + capability probe. |
| `EditorComponent / UndoStack / KillRing` | class | Editing primitives. |

## Shipped components

Ready-to-use components in `tui/components/`.

| Component | Purpose |
|---|---|
| `Box · Text · Spacer` | Layout and static text. |
| `Input · Editor` | Single-line and multi-line editing (undo, kill-ring). |
| `Markdown · Diff` | Rendered markdown and unified diffs. |
| `Image` | Inline images via Kitty / iTerm2. |
| `SelectList · SettingsList` | Menus and configuration UIs. |
| `Loader · CancellableLoader` | Spinners with optional cancellation. |

## Image protocols

Chosen by capability detection from the environment.

| Protocol | Notes |
|---|---|
| `Kitty Graphics` | Pixel-accurate inline images on Kitty-class terminals. |
| `iTerm2 inline` | Inline images on iTerm2 and compatibles. |
| `Dimension parsers` | Pure-Python PNG / JPEG / GIF / WebP — no Pillow. |
