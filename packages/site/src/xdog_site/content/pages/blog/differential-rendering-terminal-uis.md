---
title: Flicker-Free Terminal UIs with Differential Rendering
description: >
  How the tui package repaints only what changed — staying in the main terminal
  buffer, no alternate screen — to build smooth, scrollback-friendly TUIs.
date: 2026-06-18 14:00:00
tags: [tui, terminal, rendering]
---

The classic way to build a full-screen terminal app is to switch to the
alternate screen buffer and repaint everything each frame. It works, but it
throws away your scrollback and can flicker.

The tui package takes a different approach. Components return plain lines of
text; the engine diffs the new frame against the previous one and emits only the
escape sequences needed to update the lines that actually changed. It renders in
the main buffer, so your history stays intact.

On top of that sits a complete input layer: a key parser that understands xterm
escape sequences and the Kitty keyboard protocol (including modifiers and
backtab), plus inline image support through the Kitty and iTerm2 graphics
protocols with pure-Python image sizing — no Pillow dependency.

It is the rendering foundation the coding CLI and the flow workflow builder are
both built on, which is why they feel responsive even while an agent streams
output.
