"""Workflow scheduling (docs/scheduling.md).

A top-level ``schedule`` block declares how a workflow fires on its own (timer or
hook). It is declarative config for ``xdog-flow install`` — the engine ignores it.
flow.scheduler renders the systemd units / crontab lines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flow.builder.serialize import workflow_to_dict
from flow.errors import WorkflowValidationError
from flow.loader import parse_workflow, validate_workflow
from flow.models import ScheduleDef
from flow.scheduler.systemd import (
    cron_to_oncalendar,
    render_listener_service,
    render_timer_crontab,
    render_timer_units,
)


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


# --- Phase 2: systemd / crontab unit rendering -----------------------------

_B = Path("/home/u/.local/share/xdog-flow/triage")


def test_timer_every_units() -> None:
    r = render_timer_units("triage", _B, ScheduleDef(mode="timer", every="15m", inputs=(("report", "x"),)))
    assert set(r.files) == {"triage.service", "triage.timer"}
    assert r.enable == ("triage.timer",)
    tmr = r.files["triage.timer"]
    assert "OnUnitActiveSec=15min" in tmr and "Persistent=true" in tmr
    svc = r.files["triage.service"]
    assert "Type=oneshot" in svc
    assert "Environment=FLOW_RUN_ID=triage" in svc
    assert 'Environment=FLOW_INPUTS={"report": "x"}' in svc
    assert f"ExecStart=/usr/bin/python3 {_B}" in svc


def test_timer_cron_units() -> None:
    r = render_timer_units("triage", _B, ScheduleDef(mode="timer", cron="*/15 * * * *"))
    assert "OnCalendar=*-*-* *:*/15:00" in r.files["triage.timer"]


def test_timer_no_inputs_omits_env() -> None:
    r = render_timer_units("t", _B, ScheduleDef(mode="timer", every="1h"))
    assert "FLOW_INPUTS" not in r.files["t.service"]


@pytest.mark.parametrize(("cron", "oncal"), [
    ("*/15 * * * *", "*-*-* *:*/15:00"),
    ("0 9 * * 1-5", "Mon..Fri *-*-* 9:0:00"),
    ("30 2 1 * *", "*-*-1 2:30:00"),
    ("0 0 * * 0", "Sun *-*-* 0:0:00"),
])
def test_cron_to_oncalendar(cron: str, oncal: str) -> None:
    assert cron_to_oncalendar(cron) == oncal


def test_cron_bad_dow_raises() -> None:
    with pytest.raises(ValueError, match="day-of-week"):
        cron_to_oncalendar("0 0 * * xyz")


def test_crontab_fallback_cron() -> None:
    line = render_timer_crontab("triage", _B, ScheduleDef(mode="timer", cron="*/15 * * * *"))
    assert line.startswith("*/15 * * * * FLOW_RUN_ID=triage /usr/bin/python3 ")


def test_crontab_fallback_every_minutes() -> None:
    line = render_timer_crontab("t", _B, ScheduleDef(mode="timer", every="30m"))
    assert line.startswith("*/30 * * * *")


def test_crontab_fallback_subminute_raises() -> None:
    with pytest.raises(ValueError, match="sub-minute"):
        render_timer_crontab("t", _B, ScheduleDef(mode="timer", every="30s"))


def test_listener_service_render() -> None:
    svc = render_listener_service()
    assert "Description=xdog-flow shared hook listener" in svc
    assert "flow.scheduler.listener --registry" in svc
    assert "Restart=on-failure" in svc
    assert "WantedBy=default.target" in svc
