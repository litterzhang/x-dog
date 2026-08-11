# Where a skill's body belongs, for prompt caching

Status: **partly done** — `Agent` now owns placement; `coding` not yet migrated · Raised while adding `skills:` to flow agent
nodes · Owner: whoever next touches `xdog-coding`'s system prompt

## Done since this was written

`Agent` takes `skills=[...]` and decides placement itself: a session-scoped skill
goes in the system prompt, a `scope: turn` one goes in as a message. Resolution
stays with the caller — flow looks beside its workflow and in packages, coding in
its group and shared directories — because only the caller knows where to look.
Placement is the same everywhere because only one place decides it.

flow is migrated. **`coding` is not**: it still builds a skills section into
`build_system_prompt` and calls `_rebuild_system_prompt()` when the active set
changes, which is the case this note is about.

## What was true when this was written

**flow** (`skills_preamble` in `SdkRunner` / `_run_agent`): the body goes at the
**front of the system prompt**, and a node's system prompt is built once and
never changed. That is the good case — a stable prefix, cacheable in full.

**coding** (`agent_session._skills_section` → `build_system_prompt`): the body is
also in the system prompt, so the original worry — "it might be in a user
message" — does not apply. But `_rebuild_system_prompt()` is called whenever the
active skill set changes (`agent_session.py:101,108,117,162`), and a skill with
`expires_after_turn` changes it **every turn**.

## The thing to actually look at

Prompt caching keys on the **prefix**. The system prompt is the very first thing
in it, so editing the system prompt invalidates the cache for the entire
conversation — not just for the part that changed.

That inverts the intuition. For a skill that is *declared once and never
changes*, the system prompt is exactly right. For a skill that is *activated and
expired mid-conversation*, putting the body in the system prompt is the most
expensive available choice: every activation and every expiry re-sends the whole
conversation uncached.

Appending the body as a message instead would keep the cached prefix intact,
because messages accumulate at the end. That is worth measuring before changing
anything — the claim here is about how caching works, not a measurement of what
it costs in this codebase.

## The distinction that decides it

| | Skill set | Where the body belongs |
|---|---|---|
| flow agent node | declared in the workflow, fixed for the run | system prompt (already) |
| coding session | activated by `/slug`, may expire after a turn | probably a message, not a system-prompt edit |

So this is not one bug with one fix. It is: *static skills belong in the prefix,
dynamic skills belong after it*, and coding currently puts both in the prefix.

## Before changing coding

- Measure it. Count cache hits with and without an active turn-scoped skill; the
  cost may be small if sessions are short.
- Check whether the summary section (one line per skill on disk) is stable. If it
  is, it can stay in the system prompt regardless — it is only the *bodies* that
  come and go.
- Keep `expires_after_turn` working. Unloading exists so a skill's instructions
  stop applying, and moving where the body lives must not quietly make it
  permanent.
