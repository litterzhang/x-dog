---
title: Overview
---

# tui

*Terminal UI library with differential rendering.*

A string-based component toolkit for terminal apps: components return lines, the
engine diffs frames and repaints only what changed — no alternate screen, no
flicker.

Ships a full key-event parser (xterm + Kitty keyboard protocol) and inline image
support via the Kitty and iTerm2 graphics protocols.

## Highlights

- Differential line-based rendering in the main terminal buffer
- Rich key parsing: modifiers, Kitty protocol, backtab, function keys
- Inline images (Kitty / iTerm2) with pure-Python PNG/JPEG/GIF sizing
- Editor component, autocomplete, fuzzy match, kill-ring, keybindings

## Try it

tui is a library, not a CLI — import it into your terminal app:

```python
from tui import TUI, Container, Component
```
