"""flow.scheduler.systemd — render systemd user units (and crontab lines) for a
scheduled workflow.

Pure string rendering: given a :class:`~flow.models.ScheduleDef`, the installed
bundle path, and a name, produce the unit-file text.  No OS side effects — the
installer (``xdog-flow scheduling install``) writes these and calls ``systemctl``.  See
docs/scheduling.md.

- **timer** → a oneshot ``.service`` (runs ``python <bundle>``) + a ``.timer``
  (``every`` → ``OnUnitActiveSec``, ``cron`` → translated ``OnCalendar``).
- **hook** → a per-workflow ``listen`` registry entry served by the ONE shared
  ``xdog-flow-listener.service`` (rendered by :func:`render_listener_service`).
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from flow.models import ScheduleDef

# The shared hook listener service name (one per host; see docs/scheduling.md §5).
LISTENER_SERVICE = "xdog-flow-listener"


@dataclass(frozen=True)
class RenderedUnits:
    """The unit files to write for one scheduled workflow.

    ``files`` maps a unit filename to its text; ``enable`` names the unit systemctl
    should ``enable --now`` (the timer, or the listener service for hooks).
    ``crontab_line`` is set instead of ``files`` on a systemd-less timer fallback.
    """

    files: dict[str, str]
    enable: tuple[str, ...]
    crontab_line: str | None = None


_EVERY_UNIT = {"s": "s", "m": "min", "h": "h", "d": "d"}

# A Type=oneshot unit with no explicit bound inherits systemd's
# DefaultTimeoutStartSec — 90s on most distributions — which would kill any
# workflow that talks to a model. Bound every scheduled run generously by
# default; a workflow tunes it with schedule.timeout.
DEFAULT_SCHEDULE_TIMEOUT = "1h"


def _every_to_onactivesec(every: str) -> str:
    """'15m' -> '15min', '2h' -> '2h', '30s' -> '30s', '1d' -> '1d'."""
    n, unit = every[:-1], every[-1]
    return f"{n}{_EVERY_UNIT[unit]}"


def _step_field(field: str, *, what: str) -> str:
    """Translate one cron time field to systemd calendar syntax.

    The step forms differ and systemd rejects cron's: cron writes ``*/4`` ("every
    4th"), systemd writes ``<start>/<step>`` — ``0/4``. Passing ``*/4`` straight
    through produces a unit that loads as ``bad-setting``, which surfaces only when
    someone reads ``systemctl status`` days later.
    """
    if field.startswith("*/"):
        step = field[2:]
        if not step.isdigit():
            raise ValueError(f"cannot translate cron {what} {field!r} to systemd")
        return f"0/{step}"
    if "/" in field and not field.split("/", 1)[0].isdigit():
        raise ValueError(f"cannot translate cron {what} {field!r} to systemd")
    return field


def cron_to_oncalendar(cron: str) -> str:
    """Translate a 5-field cron expression to a systemd ``OnCalendar`` value.

    cron fields: minute hour day-of-month month day-of-week.  systemd calendar is
    ``DOW YYYY-MM-DD HH:MM:SS`` — for the common subset we emit ``DOW *-MM-DD
    HH:MM:00`` with ``*``, lists and ranges passed through, and cron's ``*/N``
    steps rewritten to systemd's ``0/N`` (see :func:`_step_field`).  Day-of-week
    numbers (0-6, 0=Sun) map to Mon..Sun names.

    Raises ``ValueError`` for a construct we cannot faithfully translate, so the
    installer can fall back to a real crontab line instead of mis-scheduling.
    """
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"cron must have 5 fields, got {cron!r}")
    minute, hour, dom, month, dow = parts

    # Day-of-week: systemd wants names (Mon..Sun) or a range of them.
    dow_out = ""
    if dow != "*":
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        try:
            if "-" in dow:
                a, b = dow.split("-", 1)
                dow_out = f"{names[int(a) % 7]}..{names[int(b) % 7]}"
            elif "," in dow:
                dow_out = ",".join(names[int(x) % 7] for x in dow.split(","))
            else:
                dow_out = names[int(dow) % 7]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"cannot translate cron day-of-week {dow!r} to systemd") from exc

    date = f"*-{month}-{dom}"
    time = f"{_step_field(hour, what='hour')}:{_step_field(minute, what='minute')}:00"
    return f"{dow_out} {date} {time}".strip() if dow_out else f"{date} {time}"


def _inputs_env(schedule: ScheduleDef) -> str:
    """The ``FLOW_INPUTS`` env value (compact JSON) or empty when no inputs."""
    if not schedule.inputs:
        return ""
    return json.dumps({k: v for k, v in schedule.inputs}, sort_keys=True)


def render_timer_units(
    name: str, bundle_dir: Path, schedule: ScheduleDef, *, python: str = "/usr/bin/python3"
) -> RenderedUnits:
    """Render the ``.service`` + ``.timer`` for a timer-mode workflow."""
    assert schedule.mode == "timer"
    inputs = _inputs_env(schedule)
    env_lines = f"Environment=FLOW_INPUTS={inputs}\n" if inputs else ""
    service = (
        "[Unit]\n"
        f"Description=flow workflow: {name}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"TimeoutStartSec={_every_to_onactivesec(schedule.timeout or DEFAULT_SCHEDULE_TIMEOUT)}\n"
        f"Environment=FLOW_RUN_ID={name}\n"
        f"{env_lines}"
        f"ExecStart={python} {bundle_dir}\n"
    )
    if schedule.every is not None:
        on = f"OnUnitActiveSec={_every_to_onactivesec(schedule.every)}\nOnBootSec=1min\n"
    else:
        assert schedule.cron is not None
        on = f"OnCalendar={cron_to_oncalendar(schedule.cron)}\n"
    jitter = (
        f"RandomizedDelaySec={_every_to_onactivesec(schedule.jitter)}\n"
        if schedule.jitter is not None
        else ""
    )
    timer = (
        "[Unit]\n"
        f"Description=timer for flow workflow: {name}\n"
        "\n"
        "[Timer]\n"
        f"{on}"
        f"{jitter}"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return RenderedUnits(
        files={f"{name}.service": service, f"{name}.timer": timer},
        enable=(f"{name}.timer",),
    )


def render_timer_crontab(
    name: str, bundle_dir: Path, schedule: ScheduleDef, *, python: str = "/usr/bin/python3"
) -> str:
    """Render a crontab line for a timer workflow (systemd-less fallback).

    ``every`` has no cron equivalent for arbitrary units, so only ``cron`` mode maps
    directly; an ``every`` fallback is approximated by the caller if needed.
    """
    assert schedule.mode == "timer"
    inputs = _inputs_env(schedule)
    prefix = f"FLOW_INPUTS={shlex.quote(inputs)} FLOW_RUN_ID={name} " if inputs else f"FLOW_RUN_ID={name} "
    cron = schedule.cron
    if cron is None:
        # Approximate "every" as a cron step where possible (minutes only), else
        # the caller should prefer systemd; here we support the minute case.
        assert schedule.every is not None
        n, unit = schedule.every[:-1], schedule.every[-1]
        if unit == "m":
            cron = f"*/{n} * * * *"
        elif unit == "h":
            cron = f"0 */{n} * * *"
        elif unit == "d":
            cron = f"0 0 */{n} * *"
        else:  # seconds — cron can't do sub-minute
            raise ValueError("crontab fallback cannot express a sub-minute 'every'")
    return f"{cron} {prefix}{python} {bundle_dir}"


def render_listener_service(python: str = "/usr/bin/python3", registry_path: Path | None = None) -> str:
    """Render the ONE shared hook-listener service (docs/scheduling.md §5.1).

    It reads the install registry to route every hook workflow's events; installing
    another hook workflow updates the registry and reloads this service.
    """
    reg = str(registry_path) if registry_path is not None else "%S/xdog-flow/registry.json"
    return (
        "[Unit]\n"
        "Description=xdog-flow shared hook listener\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={python} -m flow.scheduler.listener --registry {reg}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
