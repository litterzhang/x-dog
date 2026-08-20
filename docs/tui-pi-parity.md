# XDOG–Pi TUI parity status

Baseline: 2026-08-20. This document compares XDOG with the local `pi-mono` reference used during the TUI refactor. It records user-visible terminal capabilities rather than requiring API or implementation equivalence.

## Completed parity

XDOG now covers the core interactive behavior needed by coding and Claw:

- differential main-buffer rendering with native terminal scrollback;
- optional alternate-screen lifecycle;
- fragmented CSI/UTF-8 input framing and atomic bracketed paste;
- Kitty keyboard negotiation, key press/repeat/release parsing, alternate key codes, and `modifyOtherKeys` fallback;
- grapheme/display-cell-aware multiline prompt layout and hardware cursor placement;
- retained chat history and collapse/expand behavior;
- FIFO queued messages, cancellation restoration, and stale-event rejection;
- real-time, ID-keyed tool progress and canceled tool states;
- structured text/image tool-result foundations and Kitty/iTerm2 image components;
- run-scoped Claw gateway acknowledgement, cancellation, FIFO behavior, reconnect filtering, and atomic cross-channel admission;
- overlay focus restoration, Markdown transforms, tool-renderer registration, a remote-session abstraction, and basic fullscreen scrolling/search/selection primitives.

Primary implementations:

- `packages/tui/src/xdog/tui/tui.py`
- `packages/tui/src/xdog/tui/stdin_buffer.py`
- `packages/tui/src/xdog/tui/terminal_protocol.py`
- `packages/tui/src/xdog/tui/components/prompt_editor.py`
- `packages/coding/src/xdog/coding/modes/interactive/interactive_mode.py`
- `packages/claw/src/xdog/claw/channels/tui/tui_app.py`

## Remaining gaps

### P1 — Full fullscreen viewport integration

`TUI(fullscreen=True)` and `ScrollView` provide the basic alternate-screen, follow-end, scrolling, search, selection, overscan, and scrollbar primitives. Coding and Claw do not yet expose a complete Pi-style fullscreen experience with:

- application-level viewport composition;
- interactive search prompt and result navigation;
- mouse-wheel scrolling and drag selection;
- scrollbar dragging;
- measured row-height virtualization;
- offscreen Kitty image lifecycle management.

Relevant XDOG code:

- `packages/tui/src/xdog/tui/tui.py`
- `packages/tui/src/xdog/tui/components/scroll_view.py`

Pi reference:

- `packages/tui/src/tui-alt-screen.ts`
- `packages/tui/src/components/scroll-view.ts`
- `packages/tui/src/alt-screen-search.ts`

Main-buffer mode remains XDOG’s deliberate default because it preserves ordinary shell scrollback.

### P1 — Interactive selectors

XDOG supports scriptable slash-command flows for models, sessions, branches, thinking, permissions, and compaction. It does not yet provide Pi’s focused selector surfaces for:

- model and scoped-model selection;
- sessions and branch/tree navigation;
- settings and theme selection;
- project trust and authentication workflows.

Relevant XDOG code:

- `packages/coding/src/xdog/coding/core/slash_commands.py`
- `packages/coding/src/xdog/coding/modes/interactive/components/custom_editor.py`

### P1 — Extension-owned interactive UI

XDOG has lifecycle hooks, Markdown transforms, and a tool-renderer registry, but extensions cannot yet provide the broader Pi-style UI surface:

- custom editors and inputs;
- extension-owned selectors and overlays;
- contextual/width-aware Markdown transforms;
- complete registration and teardown lifecycle for interactive renderers.

Relevant XDOG code:

- `packages/coding/src/xdog/coding/core/extensions/`
- `packages/coding/src/xdog/coding/modes/interactive/tool_renderers.py`
- `packages/tui/src/xdog/tui/markdown_transforms.py`

### P1 — Complete structured media pipeline

The image components and coding tool-result path support structured images, and Claw has image-capable tool components. Remaining integration work includes:

- user image attachments in interactive prompts;
- assistant image blocks;
- preserving arbitrary mixed text/image ordering;
- terminal-aware image resize policy;
- cleanup of Kitty placements when rows leave a fullscreen viewport;
- extension renderer participation in structured media output.

Relevant XDOG code:

- `packages/tui/src/xdog/tui/components/image.py`
- `packages/tui/src/xdog/tui/terminal_image.py`
- `packages/coding/src/xdog/coding/modes/interactive/components/tool_execution.py`

### P2 — Rich Markdown rendering

XDOG Markdown supports normal terminal chat and global source transforms. Remaining fidelity differences include:

- Unicode terminal rendering for inline/block LaTeX;
- real Mermaid diagram rendering rather than textual fallback;
- streaming transform state;
- transform error isolation and contextual metadata.

Relevant XDOG code:

- `packages/tui/src/xdog/tui/components/markdown.py`
- `packages/tui/src/xdog/tui/markdown_transforms.py`

### P2 — Theme and terminal color adaptation

XDOG currently uses application default themes. Pi additionally provides runtime theme selection, theme-file watching, and terminal dark/light detection through color reports.

Relevant XDOG code:

- `packages/coding/src/xdog/coding/modes/interactive/theme.py`

### P2 — Clipboard and external-editor workflows

Text paste and terminal image display are implemented. Missing authoring workflows include:

- clipboard image acquisition;
- external editor round-trip for long prompts;
- attaching pasted images to coding and Claw requests.

### P2 — Queue and remote-session UX

The runtime supports steering and follow-up queues, while the coding UI presents a single FIFO queue. The new remote-session class is a protocol foundation, not yet wired into coding CLI/session construction with leases, event reconciliation, and reconnect/rebind UI.

Relevant XDOG code:

- `packages/agent/src/xdog/agent/agent.py`
- `packages/coding/src/xdog/coding/core/remote_session.py`

### P2 — Performance evidence

Current tests protect redraw behavior and include a basic large-transcript timing check. More complete evidence should measure:

- allocations and bytes written per frame;
- streaming transcript growth;
- large diff rendering;
- detail expansion and resize;
- fullscreen search/selection and virtualization churn;
- long-session image resource usage.

Relevant test:

- `packages/tui/tests/test_render_benchmark.py`

## Deliberate XDOG tradeoffs

- Main-buffer rendering remains the default so terminal history stays in native scrollback.
- Offscreen native scrollback is immutable; old rows are not replayed after resize or detail changes.
- Fullscreen behavior is opt-in rather than replacing the main-buffer workflow.
- XDOG keeps a dependency-light Python component model instead of cloning Pi’s TypeScript layout and extension APIs.
- Slash commands remain supported even if richer selector UI is added later.

## Verification baseline

At the time this document was written:

```text
1307 tests passed
Ruff passed
Strict mypy passed
```

Future parity work should keep main-buffer behavior as a regression boundary and add focused PTY tests for any new terminal lifecycle or fullscreen behavior.
