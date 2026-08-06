---
title: Compiling Agent Workflows to Self-Contained Python
description: >
  flow can run a workflow JSON directly or compile it to a standalone Python
  module. Here is why codegen matters and what the generated code looks like.
date: 2026-05-20 12:30:00
tags: [flow, codegen, python]
---

A JSON workflow is great for authoring and editing, but shipping an interpreter
into production is not always what you want. flow's codegen turns a workflow
definition into a single, readable Python module you can vendor, review, and run
with no engine.

The generated module keeps node outputs in a nested dict keyed by node id and
port, mirroring the runtime's own shape, so the interpreter and the compiled
form agree line-for-line. Script nodes become plain functions; agent nodes call
the ai provider directly; custom tools are imported and registered under their
manifest names.

Because the output is ordinary Python, it passes the same ruff and mypy
`--strict` gate as hand-written code. That means a workflow is not a black box —
it is source you can diff, test, and step through in a debugger.

The workflow you author, the diagram you review, and the module you deploy are
three views of the same definition. Codegen is what keeps them in sync.
