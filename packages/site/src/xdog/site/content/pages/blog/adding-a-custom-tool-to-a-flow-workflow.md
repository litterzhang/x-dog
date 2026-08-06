---
title: Adding a Custom Tool to a flow Agent — From JSON
description: >
  flow agents can call tools you declare in the workflow JSON via a module:func
  reference — loaded at run and generate time, then referenced by name like a
  built-in.
date: 2026-06-03 09:15:00
tags: [flow, tools, agents]
---

Agent nodes list tools by name. Built-ins like `filesystem` and `bash` resolve
out of the box, but real workflows need domain-specific tools. flow lets you
declare them in a top-level tools manifest that maps a name to a `module:func`
reference — symmetric with how script nodes reference their run function.

At run and generate time the reference is imported (with the workflow's own
directory on the path, so a workflow can bundle its tool `.py` files), coerced to
an AgentTool — an instance or a zero-arg factory both work — and registered under
the manifest name. That name is authoritative, so the tool the model sees matches
exactly what the node references.

Validation fails fast: a node that names a tool which is neither a built-in nor a
manifest entry errors at load time, listing the known tools. No silent typos.

The result is that custom capabilities are a small, declarative addition — no
forking the engine, no wiring code — and the interactive builder's Tools page
shows each tool's description, schema, and source alongside the built-ins.
