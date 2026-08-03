# flow — CLI Agent Backend + flow-as-a-Skill

Status: **v1 shipped** (bundle slim-down deferred — see §10) · Audience: flow
maintainers · Prerequisite: skim `subflow.md` for the "seam, not rewrite"
discipline this doc follows.

Today a flow **agent node** runs through the in-process `agent` + `ai` SDK, which
means a workflow must be configured with a **provider** (model + API key). This doc
designs an **alternative agent backend** that instead shells out to an external
coding-agent **CLI** (`claude`, `codex`, …) for one one-shot turn, plus a **skill**
that packages flow so a CLI can *generate* and *run* workflows itself. The CLI
backend is **opt-in per node** — a workflow may mix SDK and CLI agent nodes, and a
workflow authored this way is still a normal, portable flow artifact (it is **not**
restricted to CLI-only execution).

---

## 1. Why — flow as a skill a CLI installs

The driving use case is a **closed loop**: a coding-agent CLI installs flow as a
skill, uses it to turn a recurring process into a saved `workflow.json`, and then
runs that workflow — where the workflow's *agent* nodes call the **same CLI** back
for each agentic step.

```
  user → "capture my research→draft→critique process as a workflow"
      → CLI (with flow skill) writes workflow.json         (process is now crystallized)
      → CLI runs the workflow: script nodes run code,
        each CLI-agent node shells out to `claude`/`codex` for that step
      → each agent node's tools are LIMITED by the node's allow-list
```

This directly removes three burdens of flow-as-a-standalone-lib:

1. **No provider to configure.** A CLI agent node has **no provider** — it invokes
   the local `claude`/`codex`, which owns its own auth. flow no longer needs an API
   key or a provider setting for those nodes. (The SDK backend still uses a provider;
   the two coexist.)
2. **No tool catalog to maintain.** flow does **not** define or ship agent tools.
   The CLI already has a rich toolset (Read/Bash/Edit/MCP/…); a workflow node only
   **narrows** which of the CLI's tools that node may use, via an allow-list
   (§5). flow provides zero tools and tracks zero tool capabilities.
3. **Processes become reusable.** A workflow is the crystallized form of a
   recurring process — the CLI generates it once, then re-runs it deterministically
   around the non-deterministic agent steps.

Secondary benefits (unchanged from a pure-backend view): reusing a real agent
harness (the CLI's own tool loop, MCP, sandbox), a multi-CLI adapter so `codex`
drops in beside `claude`, and — for a pure-CLI workflow — a generated module and
`--portable` bundle that need not vendor `agent`/`ai`.

The **skill** itself is the new first-class deliverable — see §8.

---

## 2. The seam (what actually changes)

All agent execution is funneled through two symmetric functions:

- interpreter: `_node_agent(node, node_id, ins) -> (value, tokens)` (executor.py)
- codegen: the inlined `_run_agent(provider, model, sys, usr, tools, schema) ->
  (value, tokens)` in `templates/runtime.py.tmpl`

Both compute the same thing: `(system_prompt, user_prompt, model, tools,
output_schema) → (value, tokens)`. **A backend is an implementation of that
contract.** Everything else — `_build_inputs`, `_store_agent_output`, the driver,
retry, checkpoint, budget, fan-out, subflow — is unchanged.

```python
# The backend contract (conceptual):
class AgentRunner(Protocol):
    async def run(
        self, *, system_prompt: str, user_prompt: str, model: str,
        output_schema: dict | None,          # None -> plain text; else structured
        allowed_tools: tuple[str, ...],      # NARROW the CLI's toolset (§5); SDK ignores
        mcp_servers: tuple[tuple[str, dict], ...],  # provide MCP servers (§5.1); SDK ignores
        timeout: float,
    ) -> tuple[object, int]:                 # (structured obj OR text, tokens)
        ...
```

- **SDK runner** (today's code, kept as default): builds an `Agent`, adds the
  `submit_result` tool when structured, drains the turn.
- **CLI runner** (new): spawns the CLI subprocess, feeds the prompt on stdin,
  parses stdout, returns the same tuple.

Selection is **per node** via `node.backend` (§7): a CLI backend for that node,
else the SDK path (default). No model, edge, or scheduling semantics change →
**`interpret == compile` is untouched at the workflow level** (see §6).

---

## 3. Pluggable CLI adapters

The two CLIs differ enough that one flag template can't cover both — the
**adapter** absorbs the differences. Each adapter maps the backend contract onto
its CLI's concrete argv + stdout grammar.

### 3.1 Capability comparison (grounded, verify with `--help`)

| flow concept | `claude` (Claude Code) | `codex` (`codex exec`) |
|---|---|---|
| one-shot run | `claude -p` | `codex exec` |
| prompt via stdin | yes (≤10MB) | yes (`-` or piped) |
| output format | `--output-format json` (single JSON) | `--json` (JSONL event stream) |
| **structured output** | `--json-schema '<schema>'` → `structured_output` field | `--output-schema FILE` → final message is a JSON string |
| system prompt | `--append-system-prompt` | **no flag** — AGENTS.md or `-c` config; adapter prepends to the prompt |
| model | `--model <alias\|id>` | `-m/--model <string>` |
| custom tools | `--mcp-config <json>` + `--allowedTools mcp__<srv>__<tool>` | `~/.codex/config.toml` `[mcp_servers.*]` (+ `-c`) |
| non-interactive | `--permission-mode <mode>` | `codex exec` is already non-interactive; `--sandbox <mode>` |
| tokens | `input_tokens` + `output_tokens` (+ `total_cost_usd`) | `turn.completed.usage.{input,output}_tokens` |

**Key findings that shape the design:**
1. **Both CLIs support NATIVE structured output** — so flow's `submit_result` tool
   maps to a real flag, *not* a hand-rolled MCP sink. But the two integration paths differ (schema
   string vs schema file; `structured_output` field vs final-message JSON), so the
   adapter owns it.
2. **System prompt is asymmetric** — claude has a flag, codex does not. The codex
   adapter folds `system_prompt` into the prompt text (or writes a scratch
   AGENTS.md); the claude adapter uses `--append-system-prompt`. This is exactly
   the kind of difference the adapter, not the core, must hide.
3. **Output grammar differs** — single JSON vs JSONL. Each adapter parses its own
   stdout into `(value, tokens)`.

### 3.2 Adapter interface

```python
class CliAdapter(Protocol):
    name: str                                  # "claude-cli" / "codex-cli"
    def argv(self, *, model: str, system_prompt: str,
             output_schema: dict | None, tools: tuple[ToolSpec, ...],
             scratch: Path) -> list[str]: ...  # build the command line
    def stdin(self, *, system_prompt: str, user_prompt: str) -> str: ...
    def parse(self, stdout: str) -> tuple[object, int]:  # (value, tokens)
        ...
```

The CLI runner is adapter-agnostic: it calls `argv`/`stdin`, runs the subprocess
with `timeout`, checks the exit code, and hands stdout to `parse`.

---

## 4. Structured output (submit_result → native schema)

flow's structured agent today: append a `submit_result` tool + a system-prompt
instruction, read the object from a sink (executor.py:736-745). The CLI mapping:

- **claude**: pass `agent_output_schema(node)` to `--json-schema`; read the
  `structured_output` object from the output JSON.
- **codex**: write `agent_output_schema(node)` to a scratch file, pass
  `--output-schema <file>`; the final `agent_message` item's `text` is the JSON
  object — parse it.

Plain-text agents (no output ports, or one scalar string port) skip the schema
flags and take the final message text verbatim — same rule as
`agent_is_structured(node)` today.

**No self-built `submit_result` MCP server is needed** (both CLIs have native
schema output). The alternative — a custom MCP `submit_result(obj)` whose result
is written to a file the subprocess reads back — is documented as a *fallback* for
a CLI without native structured output, but is not the primary path.

---

## 5. Tools — declare MCP servers, narrow with an allow-list

A CLI agent node's tool config has **two independent layers**, and flow never
defines a tool's *semantics* either way — it only wires configuration:

1. **`mcp_servers`** (the extendable hatch): the node declares which MCP servers it
   wants. flow **generates the CLI's MCP config** from this and passes it to *that
   node's* CLI invocation. This is how a node brings a tool the base CLI doesn't
   have — without flow shipping the tool.
2. **`allowed_tools`** (the narrowing): an allow-list bounding which of the now-
   available tools (CLI built-ins **and** the declared MCP servers' tools) the node
   may actually call. Forwarded to the CLI's own allow-list mechanism.

```jsonc
// a CLI agent node
{
  "id": "research",
  "type": "agent",
  "backend": "claude-cli",                 // no provider needed
  "prompt": "...",
  "mcp_servers": {                          // LAYER 1: provide MCP servers for this node
    "github": {"command": "npx", "args": ["-y", "@mcp/github"],
               "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}},   // ${ENV} interpolation, no plaintext
    "db":     {"url": "https://my-mcp.example/sse"}
  },
  "allowed_tools": ["Read", "mcp__github__create_issue"],     // LAYER 2: narrow to these
  "outputs": [ ... ]
}
```

### 5.1 `mcp_servers` — an opaque, pass-through spec (extendable)

The per-server value is stored as an **unparsed dict** (`mcp_servers: tuple[(name,
dict), …]`). flow does **not** validate its inner fields — it only *format-converts*
the dict into each CLI's config shape. So when a CLI adds a new MCP config field
(headers, timeout, transport…), flow supports it with **zero changes**. This is the
"extendable hatch": flow does format translation, not schema enforcement.

- **claude**: the servers become an `--mcp-config` JSON — `{"mcpServers": {…}}` —
  written to a scratch file (or inlined) and passed to *this node's* `claude -p`.
- **codex**: the servers become `[mcp_servers.<name>]` blocks in a scratch
  `config.toml`, passed via `-c`/`--config` to *this node's* `codex exec`.

The scratch config is per-node and ephemeral (temp file, removed after the turn),
so two nodes with different servers don't collide.

### 5.2 Secrets — `${ENV_VAR}` interpolation, never plaintext

An MCP server's `env` (and any string value) may reference an environment variable
as `${VAR}`. flow resolves it against the **run-time environment** when it writes
the scratch config — the workflow JSON carries the *reference*, not the secret.
An unset `${VAR}` is an error (fail fast), not a silent empty. This honours the
"no hardcoded secrets" rule: a shared `workflow.json` never contains a token.

### 5.3 The allow-list

- **Mapping.** `allowed_tools` → the CLI's allow-list flag. An element is either a
  CLI built-in tool name (`Read`, `Bash`, `WebSearch`) or an MCP tool named
  `mcp__<server>__<tool>` — flow passes the string through verbatim; it does not
  know or check what the name means.
  - **claude**: `--allowedTools "Read,mcp__github__create_issue"`.
  - **codex**: narrowing via its config + `--sandbox <mode>` (codex has no
    per-tool-name allow-list; the sandbox mode bounds capability — an honest
    adapter difference).
- **Default: empty = no tools.** No `allowed_tools` → a pure text/structured turn
  at the tightest non-interactive posture (claude: default permission mode; codex:
  `read-only` sandbox). No bypass flag.
- **Security.** flow never emits a blanket bypass/`--dangerously-*` flag by
  default. Broadening is only done when the author explicitly sets a per-node/
  per-run permission mode — never implicitly.

### 5.4 What flow still does NOT own

flow does not define tools, does not ship tool implementations, and does not run
MCP servers *itself* — the server process is spawned by the CLI from the config
flow generated. flow's job is purely: (declare → generate config → pass to the
CLI) and (allow-list → forward). Contrast the SDK backend, whose tools ARE
flow-provided Python factories (`module:attr` → `AgentTool`); the two tool models
are different by design and not shared.

### 5.5 `interpret == compile` for tool config

Both engines generate the **byte-identical** scratch MCP config from the same
`mcp_servers` spec (same `${ENV}` resolution, same JSON/TOML serialization) and
pass the same allow-list flags. The config-generation helper is shared (interpreter
calls it; the generated module inlines the same function), so an agent turn's tool
setup is identical across engines by construction — a parity test covers a node
with an MCP server + allow-list.

---

## 6. `interpret == compile`

The invariant holds because **both engines shell the SAME CLI command**:

- interpreter: the CLI runner builds argv/stdin and runs the subprocess.
- generated module: emits a `subprocess.run([...])` with the identical argv +
  stdin, and the same stdout-parsing code (a small helper inlined from the
  adapter, or — since the module may now import `flow` for the subflow case
  anyway — a shared parser).

So an agent turn is byte-identical across engines *by construction* — same binary,
same flags, same parse. This is the fan-out/subflow pattern again: parity via
"one shared execution path," not via re-proving two implementations agree.

Testing does **not** invoke a real `claude`/`codex`. A fake CLI (a tiny script on
`PATH`, or `FLOW_CLI_BIN` pointing at a stub that echoes a canned JSON envelope)
lets both engines run and be asserted equal — the same stubbing discipline the SDK
backend uses with `stream_fn_factory`.

---

## 7. Selection & coexistence (per-node backend, no provider)

The backend is chosen **per agent node**, so one workflow can freely mix SDK and
CLI agent nodes — the workflow stays a normal, portable artifact (the user's
requirement: a skill-generated workflow is *not* CLI-only).

- **`node.backend`** (new, optional): `"claude-cli"` / `"codex-cli"` selects a CLI
  backend for that node; absent → the SDK backend (default, unchanged).
- **No provider on a CLI node.** A CLI agent node ignores `wf.provider` and needs
  no API key — the CLI owns auth. `wf.provider` is only consulted for SDK nodes, so
  a **pure-CLI workflow may omit `provider` entirely**. (Validation: `provider` is
  required only if the workflow has at least one SDK agent node.)
- **`node.model`** maps to the CLI's `--model` (claude aliases `sonnet/opus/haiku`
  or a full id; codex a free-form model string). Absent → the CLI's own default
  model (flow does not force one).
- **Binary discovery.** The CLI is found on `PATH`; `FLOW_CLI_BIN` overrides it and
  is the test-stub hook. A per-adapter default binary name (`claude`, `codex`).
- A workflow with no CLI node never spawns a subprocess; a workflow with no SDK
  node never imports `agent`/`ai`.

---

## 8. The skill (flow packaged for a CLI)

The new first-class deliverable: a **skill** that teaches a coding-agent CLI to
author and run flows. It is what turns flow-the-lib into a CLI capability.

- **Contents.** The skill bundles: (a) a concise spec of the workflow JSON schema
  (nodes, ports, edges, conditions, fan-out, subflow, CLI agent nodes) distilled
  from `models.py`/`loader.py`; (b) worked examples (the shipped `examples/*.json`,
  including a CLI-agent one); (c) the exact commands — `xdog-flow validate`,
  `xdog-flow run`, `xdog-flow generate` — with when-to-use guidance; (d) the rule
  that a CLI agent node uses `backend`, no `provider`, and an `allowed_tools`
  narrow-list.
- **Loop.** The CLI, guided by the skill, writes `workflow.json`, `validate`s it
  (fast structural feedback — the loader's errors are actionable), then `run`s it.
  Agent nodes marked `claude-cli`/`codex-cli` shell back to the CLI for their step.
- **Generation uses existing surface.** No new authoring API — the skill drives the
  same `loader` + `graph` + CLI that a human uses. The skill is mostly
  documentation + command wrappers + examples, not new code. This keeps the skill
  thin and the JSON schema the single source of truth.
- **Portability.** Because a generated workflow can still contain SDK nodes and is a
  plain JSON artifact, a workflow the CLI produces is shareable and runnable outside
  the CLI (with a provider) — it is crystallized process, not a CLI-lock-in format.

Delivery: the skill lives alongside flow (e.g. a `skill/` directory or a generated
`SKILL.md` + example pack) and is installable into `claude`/`codex` per each tool's
skill mechanism. The skill's own authoring is a separate, doc-heavy task tracked in
§11.

---

## 9. Dependency reduction (the payoff)

**Status: partially shipped.**

- **Interpreter (done)**: the CLI runner imports no `agent`/`ai` — only `asyncio`
  subprocess + `json`. `execute()` over a pure-CLI workflow builds no SDK wiring and
  needs no provider.
- **No provider config (done)**: a pure-CLI workflow carries no API key and no
  provider — the lib's configuration burden disappears for that case.
- **Codegen/bundle slim-down (deferred)**: a generated module still imports
  `agent`/`ai` at the top today (the template's SDK helper block is unconditional),
  so a pure-CLI `--portable` bundle still vendors them. Dropping it needs a
  conditional-import template refactor — emit the SDK imports + `ToolRegistry` +
  `_run_agent` block only when the workflow has an SDK agent node. Tracked as a
  follow-up.
- **Unchanged**: script nodes, scheduler, checkpoint, fan-out, subflow, wire format.

A mixed workflow vendors `agent`/`ai` for its SDK nodes regardless; the fully-lean
pure-CLI bundle arrives with the deferred refactor.

---

## 10. v1 scope & non-goals

**v1 delivers:** the `AgentRunner`/`CliAdapter` seam; a `claude` adapter and a
`codex` adapter; a per-node `backend` selector with **no provider** required for
CLI nodes; native structured output via each CLI's schema flag; `allowed_tools`
that narrows the CLI's toolset (flow provides none) **plus a per-node `mcp_servers`
pass-through spec** that flow format-converts into the CLI's MCP config (with
`${ENV}` secret interpolation); codegen that emits the subprocess call; a fake-CLI
parity test; a dependency-lean bundle for pure-CLI workflows; and a **skill** (§8)
packaging flow for a CLI to author + run flows.

**Non-goals (v1):**
- No streaming of intermediate CLI events into flow's trace (only the final
  value + tokens are captured; the CLI's own logs are its concern).
- No cost aggregation beyond token counts (codex reports no cost; claude does —
  flow accounts tokens uniformly, cost is out of scope).
- No mixing an SDK tool and a CLI MCP tool on the same node.
- No auto-installing the CLI; it must be on `PATH`.
- The SDK backend stays the default; CLI is opt-in per node.

---

## 11. Risks

1. **CLI flag drift.** Both CLIs' flags are version-dependent (codex docs are now
   redirect stubs; claude added `--json-schema` only in a recent build).
   *Mitigation:* each adapter pins the flags it uses and has a `--version` probe;
   a failing probe yields a clear error, not a mis-parse.
2. **Structured-output shape drift.** `structured_output` field (claude) vs
   final-message JSON (codex) can change. *Mitigation:* the adapter's `parse`
   is the single point of change; parity stub tests catch a regression.
3. **System-prompt asymmetry** (codex has no flag). *Mitigation:* the codex
   adapter folds it into the prompt deterministically; a test asserts the folded
   prompt is byte-identical across engines.
4. **Auth / environment.** The CLI must be authenticated out-of-band (claude
   login / API key; codex login). *Mitigation:* documented prerequisite; a dry-run
   mode uses the fake CLI so CI never needs real auth.
5. **Token accounting differences.** claude reports `total_cost_usd`, codex does
   not; both report input/output tokens. *Mitigation:* flow uses only
   input+output tokens for its budget breaker, which both provide.
6. **MCP secrets in workflow JSON.** An `mcp_servers.env` could leak a token if
   authored as plaintext. *Mitigation:* `${ENV_VAR}` interpolation resolved from
   the run-time environment only; the JSON carries the reference, never the secret;
   an unset var fails fast. A lint/validation warns on a plaintext-looking secret.
7. **MCP config divergence across engines.** The scratch config both engines write
   must be byte-identical. *Mitigation:* a single shared config-generation helper
   (interpreter calls it; the generated module inlines it); an MCP-config parity
   test.

---

## 12. Phased delivery (TDD, parity-gated)

1. **Seam.** Extract the `AgentRunner` contract; make the SDK path an
   `SdkRunner` behind it (no behaviour change; full suite still green). Gate.
2. **Model + loader.** Add `node.backend`; make `wf.provider` required only when a
   workflow has an SDK agent node (a pure-CLI workflow validates with no provider);
2. **Model + loader.** Add `node.backend`; make `wf.provider` required only when a
   workflow has an SDK agent node (a pure-CLI workflow validates with no provider);
   parse `allowed_tools` and the opaque `mcp_servers` spec; serialize round-trip.
   Unit tests.
3. **CLI runner + claude adapter.** Interpreter side: spawn, stdin, parse; a
   fake-`claude` stub test (via `FLOW_CLI_BIN`) for text + structured + allow-list.
   Gate.
4. **codex adapter.** Same contract, second adapter; fake-`codex` stub test,
   including the system-prompt folding and JSONL parse.
5. **Codegen.** Emit the subprocess call + shared parser; a workflow with no CLI
   node still emits no subprocess code (regression); a pure-CLI module imports no
   `agent`/`ai`. Cross-engine parity via the fake CLI. Gate.
6. **Tools + MCP config + bundle.** `allowed_tools` → each adapter's allow-list
   flag; `mcp_servers` → each adapter's scratch MCP config (with `${ENV}` secret
   interpolation), shared by both engines; a pure-CLI `--portable` bundle drops
   `ai`/`agent` vendoring. Allow-list + MCP-config parity tests.
7. **Skill + example.** A `cli-agent` example (a workflow with a `claude-cli` agent
   node, still runnable with an SDK provider too) and the skill pack (§8): schema
   spec + examples + command wrappers, installable into `claude`/`codex`.

---

## 13. Verification

- `uv run ruff check packages/flow/src` · `uv run mypy --strict packages/flow/src`
  · `uv run pytest packages/flow/tests -q` (fake-CLI stubs — no real agent).
- End-to-end (manual, gated on a real CLI being installed + authed): a `claude-cli`
  and a `codex-cli` agent node run via `xdog-flow run` and via `generate` + module,
  diffing the structured output shape.
- A pure-CLI workflow's `--portable` bundle contains no `_vendor/ai` or
  `_vendor/agent`.
- `git checkout -- uv.lock` before commit.
