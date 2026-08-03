# flow — CLI Agent Backend (pluggable agent runners)

Status: draft · Audience: flow maintainers · Prerequisite: skim `subflow.md` for
the "seam, not rewrite" discipline this doc follows.

Today a flow **agent node** runs through the in-process `agent` + `ai` SDK: the
interpreter builds an `Agent(stream_fn, …)` and drains one turn; codegen inlines
the same machinery into the generated module. This doc designs an **alternative
agent backend** that instead shells out to an external coding-agent **CLI**
(`claude`, `codex`, …) as a subprocess for one one-shot turn — without touching
the scheduler, checkpoint, fan-out, or subflow layers, and **coexisting** with the
SDK backend (opt-in, existing workflows unchanged).

---

## 1. Why

- **Decouple from the `agent`/`ai` SDK.** An agent node's execution is the *only*
  place flow needs those packages. A CLI backend lets a workflow run — and a
  generated module compile — without importing `agent`/`ai`. `--portable` bundles
  then need not vendor them (a large simplification; the child module becomes a
  thin `subprocess` wrapper).
- **Reuse a real agent harness.** `claude` / `codex` bring their own tool loop,
  MCP integration, sandboxing, and auth. flow orchestrates *between* agent turns;
  the CLI owns *within* a turn.
- **Multi-CLI.** Not claude-only — the backend is a **pluggable adapter** so
  `codex` (and later others) drop in behind one interface.

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
        output_schema: dict | None,      # None -> plain text; else structured
        tools: tuple[ToolSpec, ...],     # custom tools (MCP server specs, see §5)
        timeout: float,
    ) -> tuple[object, int]:             # (structured obj OR text, tokens)
        ...
```

- **SDK runner** (today's code, kept as default): builds an `Agent`, adds the
  `submit_result` tool when structured, drains the turn.
- **CLI runner** (new): spawns the CLI subprocess, feeds the prompt on stdin,
  parses stdout, returns the same tuple.

Selection is per-workflow (and overridable per-run), e.g. `provider:
"claude-cli"` / `"codex-cli"` names a CLI backend; anything else keeps the SDK
path. No model, node, or edge semantics change → **`interpret == compile` is
untouched at the workflow level** (see §6).

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
   maps to a real flag, *not* a hand-rolled MCP sink. But the two接法 differ (schema
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

## 5. Custom tools = MCP server specs

The SDK backend's tools are Python factories (`module:attr` → `AgentTool`). A CLI
cannot load an in-process Python tool — a CLI tool must be an **external MCP
server process**. So the CLI backend redefines what a workflow's tool refs mean:

- A workflow declares tool refs as **MCP server specs** (command + args + env, or
  an HTTP URL) instead of Python factories, when its backend is a CLI.
- The adapter renders those into the CLI's config:
  - **claude**: an `--mcp-config` JSON (`{"mcpServers": {...}}`, inline or scratch
    file) plus `--allowedTools mcp__<server>__<tool>` for an explicit allow-list.
  - **codex**: a scratch `config.toml` with `[mcp_servers.<name>]` blocks, passed
    via `-c`/`--config`.
- **Default: no tools.** A CLI agent node with no tool refs runs with the tightest
  non-interactive posture (claude: default permission mode; codex: `read-only`
  sandbox) — no bypass needed for a pure text/structured turn.
- **Security:** flow never emits a blanket bypass flag by default. Tools are
  opt-in and **explicitly allow-listed** per the CLI's mechanism. A permissive
  sandbox/permission mode is only used when the workflow author explicitly
  requests it (a per-node or per-run setting), never implicitly.

This asymmetry (Python factory vs MCP server) is a real semantic difference
between the two backends and is called out in the docs: a workflow's tools are
portable across CLIs (both speak MCP) but **not** shared with the SDK backend's
Python tools.

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

## 7. Dependency reduction (the payoff)

- **Interpreter**: the CLI runner imports no `agent`/`ai` — only `asyncio`
  subprocess + `json`. `execute()` with a CLI backend touches neither package.
- **Codegen**: an agent node compiles to a `subprocess.run(["claude"/"codex", …])`
  + `json.loads`, so the generated module **does not inline `agent`/`ai`**. A
  CLI-backed workflow's `--portable` bundle drops the `ai`/`agent` vendoring
  (`_VENDORED_PACKAGES` becomes empty, or just `flow` if a subflow is present).
- **Unchanged**: script nodes, scheduler, checkpoint, fan-out, subflow, the whole
  wire format.

A workflow that mixes SDK agent nodes and CLI agent nodes is allowed but its
bundle still vendors `agent`/`ai` (for the SDK nodes); a pure-CLI workflow is the
lean case.

---

## 8. Coexistence & selection

- `provider: "claude-cli"` / `"codex-cli"` selects a CLI backend for the whole
  workflow; any other provider keeps the SDK path (existing behaviour, default).
- A per-run override mirrors `--provider` (env `FLOW_PROVIDER` already exists in
  the generated module).
- The CLI binary is discovered on `PATH`; `FLOW_CLI_BIN` overrides it (also the
  test-stub hook).
- A per-node opt-in `model`/tool posture is unchanged; a workflow with no CLI
  backend never spawns a subprocess.

---

## 9. v1 scope & non-goals

**v1 delivers:** the `AgentRunner`/`CliAdapter` seam; a `claude` adapter and a
`codex` adapter; native structured output via each CLI's schema flag; custom tools
as MCP server specs with explicit allow-listing; codegen that emits the subprocess
call; a fake-CLI parity test; a dependency-lean bundle for pure-CLI workflows.

**Non-goals (v1):**
- No streaming of intermediate CLI events into flow's trace (only the final
  value + tokens are captured; the CLI's own logs are its concern).
- No cost aggregation beyond token counts (codex reports no cost; claude does —
  flow accounts tokens uniformly, cost is out of scope).
- No mixing an SDK tool and a CLI MCP tool on the same node.
- No auto-installing the CLI; it must be on `PATH`.
- The SDK backend stays the default; CLI is opt-in.

---

## 10. Risks

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

---

## 11. Phased delivery (TDD, parity-gated)

1. **Seam.** Extract the `AgentRunner` contract; make the SDK path an
   `SdkRunner` behind it (no behaviour change; full suite still green). Gate.
2. **CLI runner + claude adapter.** Interpreter side: spawn, stdin, parse; a
   fake-`claude` stub test for text + structured. Gate.
3. **codex adapter.** Same contract, second adapter; fake-`codex` stub test,
   including the system-prompt folding and JSONL parse.
4. **Codegen.** Emit the subprocess call + shared parser; a non-CLI workflow's
   module still imports no subprocess/CLI code (regression); a CLI workflow's
   module imports no `agent`/`ai`. Cross-engine parity via the fake CLI. Gate.
5. **Custom tools.** MCP-server-spec tool refs → each adapter's config; an
   allow-list test. Bundle: drop `ai`/`agent` for a pure-CLI workflow.
6. **Docs + example.** A `cli-agent` example (a workflow with a `claude-cli`
   agent node) and a README note on the auth prerequisite.

---

## 12. Verification

- `uv run ruff check packages/flow/src` · `uv run mypy --strict packages/flow/src`
  · `uv run pytest packages/flow/tests -q` (fake-CLI stubs — no real agent).
- End-to-end (manual, gated on a real CLI being installed + authed): a `claude-cli`
  and a `codex-cli` agent node run via `xdog-flow run` and via `generate` + module,
  diffing the structured output shape.
- A pure-CLI workflow's `--portable` bundle contains no `_vendor/ai` or
  `_vendor/agent`.
- `git checkout -- uv.lock` before commit.
