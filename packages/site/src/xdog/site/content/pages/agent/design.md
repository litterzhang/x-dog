---
title: Design
---

How agent turns a model plus a set of tools into an autonomous loop — the ideas
behind the two-loop core, immutable state, and the plain-object tool model.

## A loop that turns model + tools into an agent

At the centre is `agent_loop` (`agent_loop.py`): stream the model, extract the
tool calls from the assistant message, execute them, feed the results back, and
repeat until the model stops asking for tools. That single loop is what separates
an agent from a bare completion.

It is a two-loop structure — an outer loop that drains queued follow-ups and an
inner loop that runs tools and honours steering interrupts between turns.

## Agent owns immutable state

The `Agent` wrapper (`agent.py`) holds an `AgentState` that is only ever replaced
(via `dataclasses.replace`), never mutated in place. It also owns event
subscriptions and the steering / follow-up queues, so callers observe a running
turn without reaching into its internals.

## Tools are plain objects

An `AgentTool` is name + description + JSON-schema params + an async
`execute(id, params, cancel, on_update, ctx)`. Adding a capability is a small,
testable unit — no framework base class to subclass.

A declarative layer (`ToolDef` / `@action` / `Param`) builds multi-action tools
where one tool exposes several verbs, and a registry SPI lets applications
discover tools by name.

## StreamFn decouples the loop from any provider

The loop never imports a model SDK; it depends on a `StreamFn` Protocol.
`stream_fn_from_provider` (`helpers.py`) bridges an ai provider into that
Protocol, so the same agent runs against anything the ai package can reach — or a
test double.

## Steering, follow-ups, and cancellation

Steering interrupts the current turn and skips the remaining tool calls; a
follow-up is injected after the turn completes. Both are queues with `ALL` or
`ONE_AT_A_TIME` modes. `abort()` flips an `asyncio.Event` that unwinds the loop
cooperatively.
