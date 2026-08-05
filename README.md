# x-dog

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Docs](https://img.shields.io/badge/docs-xdog.942295.xyz-informational.svg)](https://xdog.942295.xyz)

**A local-first toolkit for building, running and scheduling LLM workflows — no
control plane, no database, no hosted service.**

**本地优先的 LLM 工作流工具链 —— 无控制面、无数据库、无托管服务。**

📖 **[xdog.942295.xyz](https://xdog.942295.xyz)** — documentation, design notes and blog

---

## What this is · 这是什么

x-dog is a monorepo of small, composable packages that share one idea: **what you
build should be a file you own, running on a machine you control.**

x-dog 是一组小而可组合的包，共享同一个想法：**你构建出来的东西，应该是一份你拥有的文件，
跑在你控制的机器上。**

The centrepiece is **flow** — a typed workflow format and compiler. A workflow is
one JSON file with typed ports and explicit edge mappings. It is validated as a
whole *before* anything runs, executes locally, and compiles to a standalone
Python module that needs nothing from flow at run time.

核心是 **flow** —— 一个带类型的工作流格式与编译器。一个工作流就是一份 JSON 文件，
带类型化端口和显式的边映射。它在**任何东西跑起来之前**整图校验，本地执行，并能编译成
运行时不依赖 flow 的独立 Python 模块。

| Package | CLI | What it does · 做什么 |
|---|---|---|
| **flow** | `xdog-flow` | Typed workflow format, validator, interpreter, Python compiler, systemd scheduler |
| **ai** | `xdog-ai` | Unified LLM provider API — chat, embeddings, web search, Anthropic-compatible proxy |
| **agent** | `xdog-agent` | Agent runtime: tool calling, structured output, steering |
| **coding** | `xdog-coding` | Interactive coding-agent CLI with session management |
| **claw** | `xdog-claw` | Agent orchestration runtime |
| **tui** | — | Terminal UI library with differential rendering |
| **site** | `xdog-site` | The documentation site linked above |

---

## Quick start · 快速上手

### 1. Install · 安装

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/).
需要 **Python 3.12+** 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/litterzhang/x-dog.git
cd x-dog
uv sync
```

`uv sync` creates the virtualenv and installs every package in editable mode.
Prefix commands with `uv run`, or `source .venv/bin/activate` and drop the prefix.

`uv sync` 会创建虚拟环境并以 editable 模式装上所有包。命令前加 `uv run`，
或者 `source .venv/bin/activate` 之后省掉前缀。

### 2. Log in to a provider · 登录 provider

```bash
uv run xdog-ai providers        # what's available · 有哪些 provider
uv run xdog-ai login copilot    # device-code flow · 设备码登录
```

Credentials stay on your machine; nothing is sent anywhere except the provider itself.
凭据只存在本机；除了 provider 本身，不会发往任何地方。

### 3. Chat · 对话

```bash
# One-shot · 单次提问
uv run xdog-ai chat copilot gpt-5.6-sol "Explain the CAP theorem in three sentences."

# Interactive — omit the message · 省略消息进入交互模式
uv run xdog-ai chat copilot gpt-5.6-sol

# Handy flags · 常用参数
uv run xdog-ai chat copilot gpt-5.6-sol "Summarise today's news" --web-search
uv run xdog-ai chat copilot gpt-5.6-sol "What is in this image?" -i photo.png
uv run xdog-ai models copilot --sync     # refresh the model list · 刷新模型列表
```

### 4. Your first workflow · 第一个工作流

`packages/flow/examples/agent_calculator.json` is the smallest workflow that uses
both node kinds: a deterministic **script** node builds an arithmetic problem, and
an **agent** node solves it *by running a `bash` command* rather than doing the
arithmetic in its head.

`packages/flow/examples/agent_calculator.json` 是同时用到两种节点的最小示例：
确定性的 **script** 节点构造算式，**agent** 节点**通过执行 `bash` 命令**求解，
而不是靠脑内心算。

```jsonc
{
  "name": "agent-calculator",
  "provider": "copilot",
  "defaults": { "model": "gpt-5.6-sol" },
  "entry": "make_problem",
  "state": { "a": "347", "b": "895" },        // seeds the reserved $in source
  "nodes": [
    {
      "id": "make_problem",
      "type": "script",
      "inputs": [ {"name": "a", "schema": {"type": "integer"}},
                  {"name": "b", "schema": {"type": "integer"}} ],
      "code": "def make_problem(ctx, a, b):\n    return f'{a} + {b}'",
      "outputs": [ {"name": "problem", "schema": {"type": "string"}} ]
    },
    {
      "id": "solve",
      "type": "agent",
      "inputs": ["problem"],
      "tools": ["bash"],
      "prompt": "Compute this by running a bash command: {{$.problem}}",
      "outputs": ["answer"]
    }
  ],
  "edges": [
    {"from": "$in",          "to": "make_problem", "map": {"a": "a", "b": "b"}},
    {"from": "make_problem", "to": "solve",        "map": {"problem": "problem"}},
    {"from": "solve",        "to": "$output",      "map": {"answer": "result"}}
  ]
}
```

Note the typed ports: `a` and `b` arrive as **integers** (coerced from the strings
`"347"` / `"895"`), so `make_problem` returns `"347 + 895"` — not `"347895"`. Data
moves only along declared edges; there is no shared global state.

注意类型化端口：`a` 和 `b` 以**整数**送达（由字符串 `"347"` / `"895"` 强制转换），
所以 `make_problem` 返回 `"347 + 895"` 而非 `"347895"`。数据只沿声明的边流动，
没有共享全局状态。

**Run it · 运行**

```bash
cd packages/flow

# Validate before executing; --json gives every error at once, machine-readable
# 执行前校验；--json 一次给出全部错误，机器可读
uv run xdog-flow validate examples/agent_calculator.json
uv run xdog-flow validate examples/agent_calculator.json --json

# Offline dry run — no model calls, no login needed, but the whole graph runs
# 离线试跑 —— 不调模型、不需登录，但整张图真的跑一遍
uv run xdog-flow run examples/agent_calculator.json --dry-run --input a=12 --input b=30

# For real · 真实执行
uv run xdog-flow run examples/agent_calculator.json --input a=12 --input b=30
```

Every run prints the same envelope · 每次运行都返回同一种结果信封：

```json
{
  "success": true,
  "message": "Workflow completed",
  "output": { "result": "42" },
  "context": {
    "workflow": "agent-calculator",
    "durationMs": 3120,
    "tokensUsed": 812,
    "lastNode": "solve"
  }
}
```

**Draw it, compile it, schedule it · 画图、编译、调度**

```bash
# Topology as ASCII / Mermaid / SVG · 拓扑图
uv run xdog-flow graph examples/agent_calculator.json
uv run xdog-flow graph examples/agent_calculator.json --mermaid

# Compile to a standalone module and run it with plain python
# 编译成独立模块，用普通 python 直接跑
uv run xdog-flow generate examples/agent_calculator.json -o workflow.py
python workflow.py

# Run the companion test suites — model turns are stubbed, everything else is real
# 跑配套测试套件 —— 只 stub 模型调用，边/条件/循环全部真跑
uv run xdog-flow test examples/ --allow-script-stub

# Install a systemd timer from the workflow's own `schedule` block
# 依工作流自带的 schedule 块生成并安装 systemd timer
uv run xdog-flow scheduling install examples/digest_timer.json
```

Or edit one in the interactive terminal builder · 或用交互式终端编辑器：

```bash
uv run xdog-flow build my_workflow.json
```

You can also run an example in your browser at
**[xdog.942295.xyz/havefun/flow](https://xdog.942295.xyz/havefun/flow)**.

---

## Where to next · 下一步

| | |
|---|---|
| **[xdog.942295.xyz](https://xdog.942295.xyz)** | documentation, design notes, blog · 文档、设计笔记、博客 |
| **[packages/flow/README.md](packages/flow/README.md)** | the full workflow schema, execution model and CLI · 完整 schema、执行模型与 CLI |
| **[packages/flow/examples/](packages/flow/examples/)** | runnable examples · 可运行示例 |
| **[packages/flow/examples/depins_enrich/](packages/flow/examples/depins_enrich/)** | a case study — a workflow that runs unattended every four hours and writes real commits · 案例研究：每四小时无人值守运行并产生真实提交的工作流 |

---

## Development · 开发

```bash
uv sync                                       # install everything · 安装全部
uv run pytest packages/flow/tests -q          # test one package · 单包测试
uv run ruff check packages                    # lint
uv run mypy --strict packages/flow/src/flow   # type check
```

---

## License · 许可

Copyright (c) 2026 HugeMan <942295.xyz>

Licensed under the **GNU Affero General Public License v3.0 or later** — see
[LICENSE](LICENSE). Fork it or offer it as a service, and share your changes.

**What flow compiles for you is yours.** `xdog-flow generate` copies parts of
flow's own runtime into its output, so the AGPL would otherwise follow them into
every compiled workflow. The [flow Generated Output Exception](LICENSE-EXCEPTION.md)
— an Additional Permission under AGPL section 7, modelled on the GCC Runtime
Library Exception — lets you convey generated modules, portable bundles,
scheduling units and workflow definitions under any terms, including proprietary
and commercial ones.

**flow 为你编译出的东西归你。** `xdog-flow generate` 会把 flow 自身运行时的一部分复制进
产物；若不豁免，AGPL 就会随之进入你编译的每一个工作流。
[生成物豁免](LICENSE-EXCEPTION.md)（AGPL 第 7 条的 Additional Permission，
参照 GCC Runtime Library Exception）允许你以**任何条款**分发生成的模块、可移植 bundle、
调度单元与工作流定义，包括专有与商业条款。
