---
title: Design
---

How claw coordinates long-running agents with durable memory — the gateway daemon,
per-group runtimes, the file-based system prompt, and memory that outlives a run.

## A gateway daemon, not a one-shot process

claw runs as a long-lived gateway (`core/runtime/gateway.py`): a Unix-socket
daemon (`0600` perms) speaking a JSON-line protocol — `message` / `reset` /
`status` / `ping` — that streams text deltas back to whatever is connected. It
double-forks to daemonize and runs a scheduler poll loop, so agents keep working
between your messages.

## One orchestrator, many groups

The `Orchestrator` has a single dispatch path (`route_message`). Each group is an
isolated `GroupRuntime` that owns its workspace, transcript store, memory, goals,
skills, and tools. Per-group config (`GroupDef`) pins model, thinking level,
temperature, and max tokens, so one gateway hosts several differently-tuned
agents.

## Concurrency with a semaphore and per-group locks

A global `asyncio.Semaphore` caps concurrent agents (default 3); each group also
holds its own lock. User messages bypass the semaphore so a human is never queued
behind autonomous work. Queue modes — `collect` / `steer` / `steer-backlog` —
plus debounce and a backlog cap decide how bursts of input are folded together.

## The system prompt is files on disk

`build_system_prompt` assembles a cacheable static base plus dynamic workspace
overrides. The workspace holds `IDENTITY.md`, `AGENTS.md`, `SOUL.md`, and
`USER.md`, a `MEMORY.md` snapshot, and a one-time `BOOTSTRAP.md`. Editing those
files — or running `onboard` — is how you change who the agent is; `IDENTITY.md`'s
Name line is literally where the agent's name comes from.

## Memory that outlives a conversation

`MemoryManager` composes a `DailyLog` (`memory/YYYY-MM-DD.md`), a long-term
`MEMORY.md`, and search. With the search extra installed, `HybridMemorySearch`
does vector retrieval (sentence-transformers all-MiniLM-L6-v2 + sqlite-vec) fused
with BM25 via reciprocal-rank fusion; without it, a keyword `SimpleMemorySearch`
fallback keeps memory working.

## Goals, scheduling, and channels

A `GoalManager` runs a deterministic state machine plus an LLM planner with
script- and agent-based verification, delivering autonomous work as
`SystemInput`. A task scheduler handles cron / interval / one-off jobs. Channels
connect the gateway to the outside: a local `tui` client and a `weixin` (WeChat)
bot with long-poll monitoring and QR auth.
