---
title: Examples
---

<!-- ASCII diagrams below are generated verbatim from flow.graph.to_ascii_diagram
     over the shipped packages/flow/examples/*.json. Regenerate if an example changes. -->

Two workflows ship with flow (`packages/flow/examples/*.json`). Both are loadable
and runnable live on the [HaveFun](/havefun) page — pick the example, fill its
inputs, and watch the per-node execution log stream.

## Agent Calculator (script → agent + bash)

Two nodes: `make_problem` (a script node) turns the typed integer inputs `a` and
`b` into an arithmetic string like `"347 + 895"`; `solve` (an agent node with the
bash tool) is told not to do the math in its head — it shells out to compute the
expression and replies with the integer.

```
┌────────────────────────┐
│ make_problem [script] *│
└────────────┬───────────┘
             │ problem
        ┌────┘
┌───────▼──────┐
│ solve [agent]│
└──────────────┘
```

**What running it produces:** `make_problem` builds the expression from the
inputs, then `solve` runs a bash command and returns the answer (e.g. a=12,
b=30 → answer `"42"`). A dry-run only exercises the wiring; a real run has the
agent actually compute via bash.

## Generator ↔ Critic (bounded refine loop with web search)

Two agents in a feedback loop: `draft` writes a concise answer to a topic;
`critic` fact-checks it with the `web_search` tool and replies APPROVE or
REVISE + notes. A bounded loop edge (`critic→draft`, when the feedback contains
REVISE, loop≤2) sends the notes back so `draft` can improve the answer.

```
┌────────────────┐
│ draft [agent] *│◄───┬───critic↺draft [feedback contains:{{feedback}} loop≤2]
└────────┬───────┘    │
         │ answer     │
        ┌┘            │
┌───────▼───────┐     │
│ critic [agent]│─────┘
└───────────────┘
```

**What running it produces:** `draft` produces an answer, `critic` web-searches
to verify it; if it says REVISE the answer is rewritten and re-checked, up to
twice, before the loop settles on an APPROVEd answer. This is the canonical
generate-and-critique multi-agent pattern.

## Run them

```bash
# Offline dry-run — no LLM calls, exercises the wiring
uv run xdog-flow run packages/flow/examples/agent_calculator.json --dry-run --input a=12 --input b=30

# Render the diagram
uv run xdog-flow graph packages/flow/examples/refine_loop.json
```

Or open [HaveFun](/havefun) to run either example in the browser.
