"""CLI agent backend (docs/cli-agent.md) — model + loader (Phase 2).

An agent node may set ``backend`` (claude-cli/codex-cli) to run via an external
CLI instead of the SDK. A CLI agent node needs no provider, may narrow the CLI's
toolset with ``allowed_tools``, and may declare ``mcp_servers`` (an opaque
pass-through spec). Provider is required only when an SDK agent node is present.
"""

from __future__ import annotations

import pathlib
import stat
from typing import Any

import pytest
from flow.builder.serialize import workflow_to_dict
from flow.errors import WorkflowValidationError
from flow.executor import execute
from flow.loader import parse_workflow, validate_workflow


def _cli_wf(**node_extra: Any) -> dict[str, Any]:
    node = {"id": "a", "type": "agent", "backend": "claude-cli", "prompt": "hi", "outputs": ["out"]}
    node.update(node_extra)
    return {
        "name": "cli",
        "entry": "a",
        "nodes": [node],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "result"}}],
    }


def test_pure_cli_workflow_needs_no_provider() -> None:
    validate_workflow(parse_workflow(_cli_wf()))  # no raise, no provider


def test_sdk_agent_without_provider_rejected() -> None:
    d = {
        "name": "sdk",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "hi", "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "result"}}],
    }
    with pytest.raises(WorkflowValidationError, match="no 'provider'"):
        validate_workflow(parse_workflow(d))


def test_unknown_backend_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="unknown backend"):
        validate_workflow(parse_workflow(_cli_wf(backend="gpt-cli")))


def test_allowed_tools_without_backend_rejected() -> None:
    d = {
        "name": "x",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "hi", "allowed_tools": ["Read"], "outputs": ["o"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    }
    with pytest.raises(WorkflowValidationError, match="require a CLI"):
        validate_workflow(parse_workflow(d))


def test_mcp_servers_without_backend_rejected() -> None:
    d = {
        "name": "x",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "hi",
                   "mcp_servers": {"gh": {"url": "https://x"}}, "outputs": ["o"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    }
    with pytest.raises(WorkflowValidationError, match="require a CLI"):
        validate_workflow(parse_workflow(d))


def test_mcp_server_spec_must_be_object() -> None:
    with pytest.raises(WorkflowValidationError, match="must be an object"):
        parse_workflow(_cli_wf(mcp_servers={"gh": "not-an-object"}))


def test_cli_fields_parsed() -> None:
    wf = parse_workflow(_cli_wf(
        allowed_tools=["Read", "mcp__github__create_issue"],
        mcp_servers={"github": {"command": "npx", "args": ["-y", "@mcp/github"],
                                "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}}},
    ))
    n = wf.nodes[0]
    assert n.backend == "claude-cli"
    assert n.allowed_tools == ("Read", "mcp__github__create_issue")
    assert n.mcp_servers[0][0] == "github"
    # the spec dict is stored verbatim (opaque pass-through)
    assert n.mcp_servers[0][1]["command"] == "npx"
    assert n.mcp_servers[0][1]["env"] == {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}


def test_cli_node_roundtrips() -> None:
    wf = parse_workflow(_cli_wf(
        allowed_tools=["Read"],
        mcp_servers={"github": {"command": "npx", "args": ["-y", "@mcp/github"]}},
    ))
    assert parse_workflow(workflow_to_dict(wf)) == wf


def test_mixed_sdk_and_cli_agents_ok_with_provider() -> None:
    """A workflow may mix an SDK agent (needs provider) and a CLI agent."""
    d = {
        "name": "mixed",
        "provider": "copilot",
        "entry": "sdk",
        "nodes": [
            {"id": "sdk", "type": "agent", "prompt": "hi", "outputs": ["mid"]},
            {"id": "cli", "type": "agent", "backend": "claude-cli",
             "inputs": ["mid"], "prompt": "{{mid}}", "outputs": ["out"]},
        ],
        "edges": [
            {"from": "sdk", "to": "cli", "map": {"mid": "mid"}},
            {"from": "cli", "to": "$output", "map": {"out": "result"}},
        ],
    }
    validate_workflow(parse_workflow(d))  # no raise


# --- Phase 3: CLI runner + claude adapter (interpreter) --------------------

_FAKE_CLAUDE = '''#!/usr/bin/env python3
import sys, json
argv = sys.argv[1:]
prompt = sys.stdin.read()
schema = model = allowed = sysp = None
i = 0
while i < len(argv):
    a = argv[i]
    if a == "--json-schema": schema = argv[i+1]; i += 2; continue
    if a == "--model": model = argv[i+1]; i += 2; continue
    if a == "--allowedTools": allowed = argv[i+1]; i += 2; continue
    if a == "--append-system-prompt": sysp = argv[i+1]; i += 2; continue
    i += 1
env = {"input_tokens": 11, "output_tokens": 7}
if schema is not None:
    props = json.loads(schema).get("properties", {})
    obj = {}
    for k, v in props.items():
        t = (v or {}).get("type", "string")
        obj[k] = {"string":"S","integer":1,"number":1.0,"boolean":True,"array":[],"object":{}}.get(t,"S")
    env["structured_output"] = obj
else:
    env["result"] = "TEXT|model=%s|tools=%s|sys=%s|prompt=%s" % (model, allowed, sysp, prompt.strip())
print(json.dumps(env))
'''


def _install_fake_cli(tmp_path: pathlib.Path, script: str, name: str = "fake_claude") -> str:
    """Write a fake CLI executable and return its path (for FLOW_CLI_BIN)."""
    py = tmp_path / (name + ".py")
    py.write_text(script, encoding="utf-8")
    sh = tmp_path / name
    sh.write_text(f'#!/usr/bin/env bash\nexec python3 "{py}" "$@"\n', encoding="utf-8")
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(sh)


async def test_cli_runner_text_agent(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE))
    wf = parse_workflow(_cli_wf(model="sonnet", allowed_tools=["Read", "WebSearch"]))
    r = await execute(wf)
    out = r.runtime["out"]["result"]
    assert out.startswith("TEXT|model=sonnet|tools=Read,WebSearch|")
    assert r.runtime["tokens_used"] == 18  # 11 + 7


async def test_cli_runner_structured_agent(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE))
    d = {
        "name": "s", "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "backend": "claude-cli", "prompt": "plan",
                   "outputs": [{"name": "title", "type": "string"}, {"name": "n", "type": "integer"}]}],
        "edges": [{"from": "a", "to": "$output", "map": {"title": "title", "n": "n"}}],
    }
    r = await execute(parse_workflow(d))
    assert r.runtime["out"] == {"title": "S", "n": 1}
    assert r.runtime["state"]["a"] == {"title": "S", "n": 1}


async def test_cli_runner_no_provider_needed(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """A pure-CLI workflow runs with no provider and never touches ai/agent."""
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE))
    wf = parse_workflow(_cli_wf())  # no provider in the dict
    assert wf.provider == ""
    r = await execute(wf)  # would fail if it tried ai.provider("")
    assert "result" in r.runtime["out"]


async def test_cli_runner_nonzero_exit_raises(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    from flow.errors import WorkflowExecutionError

    bad = '''#!/usr/bin/env python3
import sys
sys.stderr.write("boom")
sys.exit(3)
'''
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, bad, name="bad_claude"))
    with pytest.raises(WorkflowExecutionError, match="exited 3: boom"):
        await execute(parse_workflow(_cli_wf()))


# --- Phase 4: codex adapter ------------------------------------------------

_FAKE_CODEX = '''#!/usr/bin/env python3
import sys, json
argv = sys.argv[1:]
prompt = sys.stdin.read()
schema_file = model = None
i = 0
while i < len(argv):
    a = argv[i]
    if a == "--output-schema": schema_file = argv[i+1]; i += 2; continue
    if a == "-m": model = argv[i+1]; i += 2; continue
    i += 1
if schema_file is not None:
    props = json.loads(open(schema_file).read()).get("properties", {})
    obj = {}
    for k, v in props.items():
        t = (v or {}).get("type", "string")
        obj[k] = {"string":"S","integer":1,"number":1.0,"boolean":True,"array":[],"object":{}}.get(t,"S")
    text = json.dumps(obj)
else:
    text = "CODEX|model=%s|prompt=%s" % (model, prompt.strip().replace(chr(10), "\\\\n"))
# JSONL event stream
print(json.dumps({"type": "thread.started"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 9}}))
'''


def _codex_wf(**node_extra: Any) -> dict[str, Any]:
    node = {"id": "a", "type": "agent", "backend": "codex-cli", "prompt": "hi", "outputs": ["out"]}
    node.update(node_extra)
    return {"name": "cx", "entry": "a", "nodes": [node],
            "edges": [{"from": "a", "to": "$output", "map": {"out": "result"}}]}


async def test_codex_text_agent(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FLOW_CLI_BIN_CODEX_CLI", _install_fake_cli(tmp_path, _FAKE_CODEX, name="fake_codex"))
    wf = parse_workflow(_codex_wf(model="gpt-5-codex"))
    r = await execute(wf)
    assert r.runtime["out"]["result"].startswith("CODEX|model=gpt-5-codex|")
    assert r.runtime["tokens_used"] == 14  # 5 + 9 from JSONL usage


async def test_codex_folds_system_prompt_into_prompt(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """codex has no system-prompt flag; the adapter folds it into stdin."""
    monkeypatch.setenv("FLOW_CLI_BIN_CODEX_CLI", _install_fake_cli(tmp_path, _FAKE_CODEX, name="fake_codex"))
    wf = parse_workflow(_codex_wf(system_prompt="You are terse.", prompt="ping"))
    r = await execute(wf)
    # the fake echoes the prompt; the system prompt must appear folded in
    assert "You are terse." in r.runtime["out"]["result"]
    assert "ping" in r.runtime["out"]["result"]


async def test_codex_structured_agent(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FLOW_CLI_BIN_CODEX_CLI", _install_fake_cli(tmp_path, _FAKE_CODEX, name="fake_codex"))
    d = {
        "name": "s", "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "backend": "codex-cli", "prompt": "plan",
                   "outputs": [{"name": "title", "type": "string"}, {"name": "n", "type": "integer"}]}],
        "edges": [{"from": "a", "to": "$output", "map": {"title": "title", "n": "n"}}],
    }
    r = await execute(parse_workflow(d))
    assert r.runtime["out"] == {"title": "S", "n": 1}
