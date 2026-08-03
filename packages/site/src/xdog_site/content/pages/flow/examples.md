---
title: Examples
---

<!-- ASCII diagrams below are generated verbatim from flow.graph.to_ascii_diagram
     over the shipped packages/flow/examples/*.json. Regenerate if an example changes. -->

Four workflows ship with flow (`packages/flow/examples/*.json`). The first two are
loadable and runnable live on the [HaveFun](/havefun/flow) page — pick the example,
fill its inputs, and watch the per-node execution log stream.

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

## Essay Writer (a sub-workflow as one node)

Three nodes, but the middle one is a whole workflow. `brief` (an agent) turns a
question into a thesis and three supporting points; `compose` is a **subflow** node
that references `./essay_compose.json` — a reusable draft → critique → revise triad
authored as its own standalone, runnable workflow; `wrap` (a script) counts the
words and gates on the critic's score. The `compose` node declares no ports: its
`{thesis, points}` inputs and `{final_essay, score}` outputs are *derived* from the
child's signature.

```
┌──────────────┐
│ brief [agent]│
└───────┬──────┘
        │ thesis, key_poin
        └─┐
┌─────────▼────────┐
│ compose [subflow]│
└─────────┬────────┘
          │ final_essay, s
        ┌─┘
┌───────▼──────┐
│ wrap [script]│
└──────────────┘
```

**What running it produces:** `brief` sets the argument; the `compose` child runs
its own draft → critique → revise internally as one opaque step, returning the
polished essay plus the critic's score; `wrap` reports the word count and whether
the score cleared the bar. The child is a complete workflow — you can `run` it on
its own. Both engines run `compose` by calling the same `execute()` on the child,
so `interpret == compile` holds by construction. See the
[*A Workflow as a Node*](/blog/a-workflow-as-a-node) post for the design.

## Run them

```bash
# Offline dry-run — no LLM calls, exercises the wiring
uv run xdog-flow run packages/flow/examples/agent_calculator.json --dry-run --input a=12 --input b=30

# Render the diagram
uv run xdog-flow graph packages/flow/examples/refine_loop.json
```

Or open [HaveFun](/havefun/flow) to run either example in the browser.
