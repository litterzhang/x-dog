"""Workflow scheduling (docs/scheduling.md) — Phase 1: schedule block.

A top-level ``schedule`` block declares how a workflow fires on its own (timer or
hook). It is declarative config for ``xdog-flow install`` — the engine ignores it.
"""

from __future__ import annotations

from typing import Any

import pytest
from flow.builder.serialize import workflow_to_dict
from flow.errors import WorkflowValidationError
from flow.loader import parse_workflow, validate_workflow


def _wf(schedule: dict[str, Any] | None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": "s",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "backend": "claude-cli", "prompt": "hi", "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    }
    if schedule is not None:
        d["schedule"] = schedule
    return d


def test_no_schedule_is_run_once() -> None:
    wf = parse_workflow(_wf(None))
    validate_workflow(wf)
    assert wf.schedule is None


def test_timer_every_parsed() -> None:
    wf = parse_workflow(_wf({"mode": "timer", "every": "15m", "inputs": {"report": "x"}}))
    validate_workflow(wf)
    assert wf.schedule is not None
    assert wf.schedule.mode == "timer"
    assert wf.schedule.every == "15m"
    assert wf.schedule.cron is None
    assert dict(wf.schedule.inputs) == {"report": "x"}


def test_timer_cron_parsed() -> None:
    wf = parse_workflow(_wf({"mode": "timer", "cron": "*/15 * * * *"}))
    validate_workflow(wf)
    assert wf.schedule is not None
    assert wf.schedule.cron == "*/15 * * * *"
    assert wf.schedule.every is None


def test_hook_parsed() -> None:
    wf = parse_workflow(_wf({"mode": "hook", "signal": "new-ticket",
                             "listen": {"type": "http", "path": "/hooks/t", "port": 8787}}))
    validate_workflow(wf)
    assert wf.schedule is not None
    assert wf.schedule.mode == "hook"
    assert wf.schedule.signal == "new-ticket"
    assert wf.schedule.listen == {"type": "http", "path": "/hooks/t", "port": 8787}


@pytest.mark.parametrize(
    ("schedule", "match"),
    [
        ({"mode": "every-minute"}, "'timer' or 'hook'"),
        ({"mode": "timer", "every": "15m", "cron": "* * * * *"}, "exactly one"),
        ({"mode": "timer"}, "exactly one"),
        ({"mode": "timer", "every": "15min"}, "like '30s'"),
        ({"mode": "timer", "cron": "* * *"}, "5-field cron"),
        ({"mode": "hook", "listen": {"type": "http"}}, "non-empty"),
        ({"mode": "hook", "signal": "s"}, "'listen' object"),
        ({"mode": "hook", "signal": "s", "listen": {"type": "grpc"}}, "listen.type"),
    ],
)
def test_schedule_rejects(schedule: dict[str, Any], match: str) -> None:
    with pytest.raises(WorkflowValidationError, match=match):
        parse_workflow(_wf(schedule))


def test_schedule_not_object_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="schedule must be an object"):
        parse_workflow(_wf("15m"))  # type: ignore[arg-type]


@pytest.mark.parametrize("schedule", [
    {"mode": "timer", "every": "30s"},
    {"mode": "timer", "cron": "0 9 * * 1-5", "inputs": {"n": 3}},
    {"mode": "hook", "signal": "sig", "listen": {"type": "file", "dir": "/tmp/drop"}},
    {"mode": "hook", "signal": "sig", "listen": {"type": "socket", "path": "/run/x.sock"}},
])
def test_schedule_roundtrips(schedule: dict[str, Any]) -> None:
    wf = parse_workflow(_wf(schedule))
    assert parse_workflow(workflow_to_dict(wf)) == wf
