---
title: Why a Ports Model Beats Shared State for Agent Workflows
description: >
  How flow moves data between nodes through named ports and explicit edge
  mappings instead of a shared global dict — and why that makes pipelines easier
  to reason about.
date: 2026-05-12 10:00:00
tags: [flow, design, workflows]
---

Most workflow engines pass data between steps through one shared, flat state
dict. It is convenient at first, but it hides the real data dependencies: any
node can read or clobber any key, and a rename in one place silently breaks a
consumer somewhere else.

flow takes the opposite stance. Every node declares typed input and output
ports, and every edge carries an explicit mapping — `nodeA.output.x` feeds
`nodeB.input.a`. The wiring is spelled out, not implied by matching key names.

That single decision buys a lot. The graph can be validated before it runs:
unknown ports, two producers feeding one input, or an unfed required input all
fail fast at load time. Port-local interpolation means a node's prompt template
only sees its own inputs, so there is no accidental coupling to unrelated state.

It also makes the workflow legible. A reader (or a generated ASCII diagram) can
see exactly which data each edge carries. When you later compile the workflow to
Python, the ports become ordinary function parameters — no magic global to
thread through.
