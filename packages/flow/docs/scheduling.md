# flow — Workflow Scheduling & `xdog-flow install`

Status: draft · Audience: flow maintainers · Prerequisite: skim `cli-agent.md`
(the bundle is the deployable artifact a scheduler wraps).

Today a workflow runs once, on demand (`xdog-flow run`, or `python <bundle>`).
This doc designs **scheduling** — making a workflow fire on its own — plus an
`xdog-flow install` command that builds a portable bundle and installs the
scheduler on Linux. Two scheduling modes:

1. **Active / timer** — fire on a schedule (every N minutes, or a cron expression).
2. **Passive / hook** — fire when an external **signal** arrives (a webhook, a file
   drop, a queue message), reusing flow's existing signal primitive.

The guiding principle (same as every prior feature): **the scheduler wraps the
built artifact; it does not change execution.** `interpret == compile` and the
whole engine are untouched — a scheduler is just something that *invokes* the
bundle with the right environment.

---

## 1. What already exists (the substrate)

Two facts make this a thin layer, not a new engine:

- **The bundle is a self-contained runnable.** `xdog-flow generate --portable`
  emits a directory whose `__main__.py` runs the workflow via `python <bundle>`,
  reading its inputs from **environment variables**: `FLOW_INPUTS` (JSON merged
  into `$in`), `FLOW_PROVIDER`, `FLOW_SIGNALS`, `FLOW_MAX_TOKENS`,
  `FLOW_RUN_ID`/`FLOW_CHECKPOINT_DIR`. So "run this workflow with these inputs /
  this signal delivered" is already expressible as a subprocess + env — exactly a
  scheduler's job.
- **flow already has a signal primitive.** A `human` node with a `signal` pauses
  the run (`WorkflowPaused`) until that signal is present in the `FLOW_SIGNALS`
  set (executor.py ~566). Passive scheduling is *delivering a signal* to a
  workflow — the mechanism is built; scheduling wires an external event to it.

The scheduler is therefore **OS glue** around a runnable artifact, not code inside
the engine.

---

## 2. Where the schedule lives — a `schedule` block in workflow.json

A top-level optional `schedule` block on the workflow (mirrors `max_concurrency`
etc. — a `WorkflowDef` field, parsed + validated + round-tripped):

```jsonc
{
  "name": "cli-triage",
  "schedule": {
    "mode": "timer",                 // "timer" | "hook"
    // --- timer mode ---
    "every": "15m",                  // "30s"/"15m"/"2h"/"1d", OR:
    "cron":  "*/15 * * * *",         //   a 5-field cron expression (mutually exclusive)
    "inputs": { "report": "..." },   // optional fixed $in for each firing
    // --- hook mode ---
    "signal": "new-ticket",          // the signal delivered on each event
    "listen": { "type": "http", "path": "/hooks/triage", "port": 8787 }
  }
}
```

- A workflow with **no** `schedule` block behaves exactly as today (run-once).
- `mode` selects the mechanism; `timer` uses `every` **or** `cron` (validation
  rejects both/neither); `hook` uses `signal` + a `listen` transport.
- `inputs` is optional per-firing seed data (env `FLOW_INPUTS`); secrets follow the
  `${ENV_VAR}` rule (never inline a token — resolved at fire time).

The block is **declarative config for the installer**, not something the engine
reads at run time — the engine still just runs once per invocation.

---

## 3. `xdog-flow install`

```
xdog-flow install <workflow.json> [--prefix DIR] [--name NAME] [--dry-run]
xdog-flow uninstall <NAME>
xdog-flow install --list
```

Steps (Linux v1):
1. **Build.** `build_bundle(wf, <prefix>/<name>)` — a `--portable` bundle is the
   deployable unit (self-contained; for a pure-CLI workflow it vendors nothing).
2. **Emit units** from the `schedule` block (§4 / §5) into the user systemd dir
   (`~/.config/systemd/user/`), or a crontab line as a fallback (§6).
3. **Enable** the unit (`systemctl --user enable --now <name>.timer` /
   `.service`), unless `--dry-run` (which prints the unit files without installing).

`--prefix` defaults to `~/.local/share/xdog-flow/<name>/` (the bundle lives here;
units reference it by absolute path). `--name` defaults to the workflow name.
`uninstall` disables + removes the units and the bundle dir. `--list` shows
installed workflows and their next-fire time (`systemctl --user list-timers`).

Nothing here touches the engine; `install` is a new CLI command + a
`flow.scheduler` module that renders unit files.

---

## 4. Active / timer scheduling — how it works

A **systemd user timer** fires a **oneshot service** that runs the bundle. Two
generated units:

`<name>.service` (the work):
```ini
[Unit]
Description=flow workflow: cli-triage

[Service]
Type=oneshot
# Absolute path to the installed bundle; env carries per-firing inputs.
Environment=FLOW_INPUTS={"report":"..."}
Environment=FLOW_RUN_ID=cli-triage
Environment=FLOW_CHECKPOINT_DIR=%S/xdog-flow/cli-triage/ckpt
ExecStart=/usr/bin/python3 /home/user/.local/share/xdog-flow/cli-triage
```

`<name>.timer` (the schedule):
```ini
[Unit]
Description=timer for flow workflow: cli-triage

[Timer]
# from "every": OnUnitActiveSec=15min ; from "cron": OnCalendar=*/15 * * * *
OnCalendar=*/15 * * * *
Persistent=true                 # catch up a missed firing after downtime

[Install]
WantedBy=timers.target
```

- **`every: "15m"`** → `OnUnitActiveSec=15min` (relative, drift-free interval).
- **`cron: "*/15 * * * *"`** → `OnCalendar=<translated>` (systemd calendar syntax
  is a near-superset of cron; the installer translates the 5-field cron to
  `OnCalendar`, rejecting constructs systemd can't express).
- Each firing is an **independent one-shot run** of the bundle — no long-lived
  process, no in-engine scheduler loop. This keeps the engine stateless and the
  failure model simple (a crashed run doesn't wedge the schedule; the next tick
  fires clean).
- `Persistent=true` gives at-least-catch-up semantics after the machine was off.
- Logs go to the journal (`journalctl --user -u <name>.service`).

**Why systemd timer over an in-process loop:** no daemon to keep alive, survives
reboot, native logging + backoff (`RestartSec`), and the run is the exact same
`python <bundle>` a human would type — so "scheduled" and "manual" runs are
identical (no divergent code path, mirroring `interpret == compile`).

---

## 5. Passive / hook scheduling — how it works

The event source varies (HTTP webhook, file-drop, queue), but the **core is one
idea**: an external event → deliver a **signal** to a fresh run of the bundle.

Installed as a **long-running systemd user service** — a tiny listener shipped with
flow (`flow.scheduler.listener`) that, on each event, spawns the bundle with the
signal set:

`<name>.service` (the listener):
```ini
[Service]
Type=simple
Environment=FLOW_BUNDLE=/home/user/.local/share/xdog-flow/cli-triage
ExecStart=/usr/bin/python3 -m flow.scheduler.listener --config <name>.listen.json
Restart=on-failure

[Install]
WantedBy=default.target
```

The listener, per the `listen` transport:
- **`http`**: binds `port`, accepts a POST to `path`; the request body (JSON)
  becomes `FLOW_INPUTS`, and it spawns `python <bundle>` with
  `FLOW_SIGNALS=<signal>`. Returns the run's `$output` (or 202 + a run id for async).
- **`file`** (an alternative transport): watches a directory; a dropped file's
  content becomes `FLOW_INPUTS`, the filename/event is the trigger.
- **`socket`**: a Unix socket for local IPC (systemd socket activation is a natural
  fit — the service starts on first connection).

Each event → **one bundle subprocess** with `FLOW_SIGNALS` carrying the declared
signal. The workflow's `human`-node-with-that-signal proceeds instead of pausing —
so the *same* workflow runs manually (paused at the signal) or via hook (signal
delivered). The passive path reuses the existing pause/resume primitive verbatim;
scheduling only supplies the signal from an external event.

### 5.1 Who starts (and supervises) the listener

**systemd owns the listener's lifecycle — flow writes no daemon/supervision code.**
All three transports share **one** listener program (`flow.scheduler.listener`);
the `listen.type` selects its internal event loop (bind a port / watch a dir /
accept a socket). What differs is how systemd brings it up:

- **Resident (http, file)** — a `Type=simple` user **service** that `install`
  enables (`systemctl --user enable --now <name>.service`). systemd starts it now,
  restarts it on crash (`Restart=on-failure`), and re-launches it on boot/login
  (`WantedBy=default.target`). The process runs continuously, looping on its
  event source.
- **On-demand (socket)** — a systemd **`.socket` + `.service` pair** using **socket
  activation**: systemd itself holds the listening socket and starts the service
  only on the first connection (and can stop it when idle). The listener need not
  run continuously; systemd is the front door. `install` enables the `.socket`.
- **No-systemd fallback** — `install` cannot supervise a long-lived process
  without an init system, so it prints a ready-to-run
  `nohup python -m flow.scheduler.listener --config … &` line (or writes it into
  the user's shell-profile startup) and documents that the user owns keeping it
  alive. Timer mode's crontab fallback (§6) needs no daemon; hook mode does — this
  is the honest limitation of a systemd-less host.

So "who starts the listener" is **systemd** (resident service, or socket-activated
pair), installed and enabled by `xdog-flow install`; the only case where the user
starts it by hand is a host without systemd.

**Why a thin listener, not a framework:** flow doesn't own the event source. The
listener is a minimal adapter (event → env → subprocess) so a webhook, a cron-job
POST, a CI callback, or a message-queue bridge all funnel through the same
"deliver a signal" door. Anything richer (auth, retries, fan-in) is the event
source's concern or a later transport.

---

## 6. Fallbacks & portability

- **crontab fallback** (when systemd-user is unavailable, e.g. a minimal
  container): `install` writes a crontab line `*/15 * * * * FLOW_INPUTS=... python
  <bundle>` for timer mode. Hook mode has no crontab analogue — it needs a
  long-lived listener, so on a systemd-less host the listener is started via a
  provided `nohup`/`&`-style shim or documented as manual.
- **Linux first (v1).** systemd user units cover the common case. macOS
  (`launchd` plist) and a container-native mode (a sidecar) are documented as
  follow-ups; the unit-rendering is behind an interface so a second backend drops
  in.
- The scheduler layer is **optional** — it lives in `flow.scheduler` and is only
  imported by `xdog-flow install`; `run`/`generate`/the engine never touch it.

---

## 7. `interpret == compile` and the engine

Untouched. Scheduling wraps the **artifact**:

- A **timer** run is `python <bundle>` — the same self-contained module the
  compiler already produces and the parity tests already gate. Scheduled ==
  manual, byte for byte.
- A **hook** run is the same bundle with `FLOW_SIGNALS` set — the signal path is
  the existing human-node mechanism, covered by `test_human.py`.

So no new execution semantics, no new parity surface: the scheduler is tested by
asserting it **renders the right unit files / env** and that the listener spawns
the bundle with the right `FLOW_SIGNALS`/`FLOW_INPUTS` — not by re-testing
execution.

---

## 8. Security

- **No secrets in unit files.** `schedule.inputs` and `mcp_servers.env` use
  `${ENV_VAR}`; the installed unit references the *variable* (systemd
  `Environment=` can pull from a root-only `EnvironmentFile=`), never the literal
  token. `install --dry-run` shows exactly what would be written so a secret can't
  leak unnoticed.
- **User-scoped by default.** Units install under `~/.config/systemd/user/` and
  run as the invoking user — no root, no system-wide daemon.
- **Hook listener is minimal + explicit.** It binds only the declared port/path,
  runs each event as an isolated subprocess (a crash can't corrupt the listener),
  and does not expose the workflow beyond the declared transport. Auth (a shared
  secret / bearer header) is a `listen` option, off by default with a warning when
  a port is public.

---

## 9. v1 scope & non-goals

**v1 delivers:** the `schedule` block (model + loader + serialize); `xdog-flow
install`/`uninstall`/`--list`; timer mode via systemd user timer (interval + cron)
with a crontab fallback; hook mode via a single listener supervised by systemd —
**http and file as resident services, socket via socket-activation** — plus a
no-systemd `nohup` fallback; `--dry-run` unit preview; docs + one scheduled example.

**Non-goals (v1):**
- No macOS/`launchd` or Windows (Linux/systemd first; interface leaves room).
- No distributed scheduling / multi-host coordination (single machine, unchanged
  non-goal).
- No in-engine scheduler loop or long-lived workflow process — every firing is a
  fresh one-shot bundle run.
- No built-in retry/alerting beyond systemd's (`Restart=`, `OnFailure=`); richer
  ops integration is later.
- No hook transports beyond http/file/socket in v1; a queue bridge (SQS/NATS) is a
  follow-up transport behind the same "deliver a signal" interface. (All three v1
  transports share one listener supervised by systemd — §5.1.)

---

## 10. Risks

1. **cron → `OnCalendar` translation.** systemd calendar syntax differs from cron
   in edge cases (step values, day-of-week). *Mitigation:* translate the common
   subset, validate at install, and fall back to a crontab line (real cron) when a
   cron expression can't be faithfully translated — never silently mis-schedule.
2. **Absolute paths / relocation.** Units hard-code the bundle path; moving the
   bundle breaks the timer. *Mitigation:* `install` owns the bundle location
   (`--prefix`), `uninstall` cleans it, and `--list` shows the path; re-install to
   relocate.
3. **Hook listener as an attack surface.** A public port running a workflow is
   dangerous. *Mitigation:* bind localhost by default, require an explicit
   `listen.host`/auth to expose it, isolate each run in a subprocess, and warn
   loudly in `install` output.
4. **Missed firings / overlap.** A long run overlapping the next tick, or downtime.
   *Mitigation:* `Persistent=true` for catch-up; `OnUnitActiveSec` (not
   `OnCalendar`) for drift-free intervals; document that a run longer than its
   period will queue (systemd serializes a oneshot service by default).
5. **Secret handling in `Environment=`.** A literal secret in a unit is
   world-readable in some setups. *Mitigation:* `${ENV}` + `EnvironmentFile=` with
   `0600`, and the `--dry-run` audit.

---

## 11. Phased delivery (TDD)

1. **Model + loader.** `WorkflowDef.schedule` (a small frozen dataclass: mode,
   every/cron, inputs, signal, listen); parse + validate (mode-specific rules,
   every-xor-cron) + serialize round-trip. Unit tests only.
2. **Unit rendering.** `flow.scheduler.systemd` renders `.service`/`.timer` (timer)
   and `.service` (hook) from a `schedule` block; a crontab renderer. Pure
   string-render tests (assert the emitted unit text), no OS side effects.
3. **`xdog-flow install`/`uninstall`/`--list`.** Wire the command: build_bundle +
   render + `systemctl --user` calls (guarded so `--dry-run` prints instead).
   Tests stub `systemctl` (a fake on PATH) and assert files written + commands run.
4. **Hook listener.** `flow.scheduler.listener` (http/file/socket) that spawns the
   bundle with `FLOW_SIGNALS`/`FLOW_INPUTS`. Tests drive the listener in-process
   against a fake bundle (a stub `python <bundle>`), asserting the right env is
   passed and the signal reaches a human node.
5. **Docs + example.** A scheduled example (a `timer` triage and a `hook` triage)
   and a README note on `journalctl`/uninstall.

---

## 12. Verification

- `uv run ruff check packages/flow/src` · `uv run mypy --strict packages/flow/src`
  · `uv run pytest packages/flow/tests -q` (unit rendering + stubbed systemctl +
  in-process listener — no real timers or ports in CI).
- End-to-end (manual, Linux): `xdog-flow install examples/cli_triage_timer.json`,
  confirm `systemctl --user list-timers` shows it, wait a tick, check
  `journalctl --user -u cli-triage.service` for the run output; `xdog-flow
  uninstall cli-triage` removes it. For hook: `install`, `curl` the webhook, see
  the run fire.
- `git checkout -- uv.lock` before commit.
