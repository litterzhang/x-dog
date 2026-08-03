"""CLI agent backend (docs/cli-agent.md) — model + loader (Phase 2).

An agent node may set ``backend`` (claude-cli/codex-cli) to run via an external
CLI instead of the SDK. A CLI agent node needs no provider, may narrow the CLI's
toolset with ``allowed_tools``, and may declare ``mcp_servers`` (an opaque
pass-through spec). Provider is required only when an SDK agent node is present.
"""

from __future__ import annotations

from typing import Any

import pytest
from flow.builder.serialize import workflow_to_dict
from flow.errors import WorkflowValidationError
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
