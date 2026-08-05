"""Seed FAQ entries for the x-dog site."""

from __future__ import annotations

FAQS: list[dict[str, str]] = [
    {
        "q": "What is x-dog?",
        "a": (
            "x-dog is a Python monorepo of composable packages for building AI agents and managing "
            "LLM deployments: a unified provider API (ai), an agent runtime (agent), a terminal UI "
            "library (tui), an interactive coding CLI (coding), an orchestration runtime with "
            "long-term memory (claw), and a typed local-first workflow format/compiler for humans "
            "and Coding Agents (flow)."
        ),
    },
    {
        "q": "Which package do I need?",
        "a": (
            "Start with ai if you just want one interface over many model providers. Add agent to "
            "get a tool-calling loop. Reach for flow when a developer or Coding Agent needs to "
            "crystallize a repeatable process as validated, Git-friendly JSON, then run, compile, or "
            "schedule it locally. tui is the "
            "rendering layer; coding and claw are ready-to-use applications built on the rest."
        ),
    },
    {
        "q": "Which model providers are supported?",
        "a": (
            "Through the ai package: Anthropic, OpenAI, Google, Amazon Bedrock, Mistral, and GitHub "
            "Copilot. Application code selects a model by id; switching providers does not require "
            "changing the calling code."
        ),
    },
    {
        "q": "How does flow's code generation work?",
        "a": (
            "A workflow is authored as JSON. You can execute it directly with the runtime, or run "
            "xdog-flow generate to compile it into a single self-contained Python module. The "
            "generated code keeps node outputs in the same nested port structure the interpreter "
            "uses, so both forms behave identically, and it passes the same ruff and mypy --strict "
            "checks as hand-written code."
        ),
    },
    {
        "q": "How do I add a custom tool to a flow agent?",
        "a": (
            "Declare it in the workflow's top-level tools manifest as a name mapped to a "
            "module:func reference, then list that name in an agent node's tools. The reference is "
            "imported at run and generate time (the workflow's own directory is on the path, so you "
            "can bundle tool files next to the JSON) and registered under the manifest name. Unknown "
            "tool names fail validation early."
        ),
    },
    {
        "q": "Can I see and edit a workflow visually?",
        "a": (
            "Yes. xdog-flow build opens an interactive terminal builder with graph, node/edge, "
            "Functions, and Tools views. A local Web UI is planned as a Workflow JSON IDE; both UI "
            "surfaces and Coding Agents edit the same file rather than a database-only model."
        ),
    },
    {
        "q": "Is flow production-ready?",
        "a": (
            "Flow ships typed ports, explicit mappings, frontier-based parallel execution, retry policies, "
            "coherent batch checkpoints, human signals, failure isolation, dynamic fan-out, subflows, "
            "standalone codegen, and local scheduling. It is production-appropriate for single-machine "
            "developer automation, but deliberately not a distributed durable-execution platform or "
            "multi-tenant hosted service."
        ),
    },
    {
        "q": "How do I install and run it?",
        "a": (
            "Clone the repository and use uv: 'uv sync' installs all workspace packages, then the "
            "console scripts are available via 'uv run' — xdog-ai, xdog-agent, xdog-coding, "
            "xdog-flow, and xdog. Python 3.11+ is required."
        ),
    },
    {
        "q": "What is the license?",
        "a": "x-dog is MIT-licensed.",
    },
]
