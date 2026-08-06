"""flow.scheduler.install — install / list / delete scheduled workflows.

Wires :mod:`flow.bundle` (build the deployable artifact) and
:mod:`flow.scheduler.systemd` (render units) to the OS: writes user unit files,
calls ``systemctl --user``, and maintains a small JSON **install registry** so
``scheduling list`` / ``scheduling uninstall`` know which units are flow's (independent of systemd's
global list).  The scheduler wraps the built bundle; it never changes execution.
See docs/scheduling.md.

All OS locations are injectable (``unit_dir``/``data_dir``/``systemctl``) so tests
run fully offline against a temp dir + a stub ``systemctl``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from xdog.flow.bundle import build_bundle
from xdog.flow.models import ScheduleDef, WorkflowDef
from xdog.flow.scheduler.systemd import (
    LISTENER_SERVICE,
    render_listener_service,
    render_timer_units,
)


def default_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "xdog-flow"


@dataclass(frozen=True)
class Installer:
    """Locations + the systemctl command; injectable for tests."""

    unit_dir: Path
    data_dir: Path
    systemctl: tuple[str, ...] = ("systemctl", "--user")
    # Interpreter for the unit's ExecStart. Only consulted with venv=False; by
    # default the bundle gets its own uv-provisioned environment instead.
    python: str = "/usr/bin/python3"
    # Path to uv; None resolves (and if necessary installs) it. Injectable for tests.
    uv: str | None = None

    # -- registry ----------------------------------------------------------
    @property
    def registry_path(self) -> Path:
        return self.data_dir / "registry.json"

    def _load_registry(self) -> dict[str, dict[str, object]]:
        p = self.registry_path
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def _save_registry(self, reg: dict[str, dict[str, object]]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _run(self, *args: str, dry_run: bool) -> None:
        cmd = [*self.systemctl, *args]
        if dry_run:
            print("  would run:", " ".join(cmd))
            return
        subprocess.run(cmd, check=False)

    # -- install -----------------------------------------------------------
    def install(
        self,
        wf: WorkflowDef,
        *,
        name: str | None = None,
        dry_run: bool = False,
        base_dir: Path | None = None,
        venv: bool = True,
    ) -> str:
        """Build the bundle + install the scheduler for *wf*; return its name.

        *base_dir* is the workflow file's directory; it travels into the bundle so
        ``run:`` script references still import once the unit runs from elsewhere.

        With *venv* (the default) the bundle is provisioned as a uv project — uv
        supplies the interpreter, the virtualenv and the dependencies — and the unit
        runs that environment. An install is then complete on its own rather than
        depending on whatever ``/usr/bin/python3`` happens to be or have. Pass
        ``venv=False`` to reuse :attr:`python` instead.
        """
        if wf.schedule is None:
            raise ValueError(f"workflow {wf.name!r} has no 'schedule' block to install")
        name = name or wf.name
        bundle_dir = (self.data_dir / name).resolve()

        python = self.python
        if not dry_run:
            build_bundle(wf, bundle_dir, base_dir=base_dir)
            if venv:
                python = str(self._provision_venv(bundle_dir))
        else:
            print(f"  would build bundle at {bundle_dir}")
            if venv:
                python = str(bundle_dir / ".venv" / "bin" / "python")
                print(f"  would run: uv sync --project {bundle_dir}")

        if wf.schedule.mode == "timer":
            self._install_timer(name, bundle_dir, wf.schedule, dry_run=dry_run, python=python)
        else:
            self._install_hook(name, bundle_dir, wf.schedule, dry_run=dry_run)
        return name

    def _resolve_uv(self) -> str:
        """Return a usable ``uv``, installing it if the host has none.

        The whole provisioning path is uv's: it supplies the interpreter, the
        virtualenv, and the packages, so a scheduled workflow never depends on
        what ``/usr/bin/python3`` happens to be or have. ``shutil.which`` alone is
        not enough — a systemd user unit gets a minimal PATH — so the usual
        install location is checked directly too.
        """
        if self.uv:
            return self.uv
        found = shutil.which("uv")
        if found:
            return found
        for candidate in (Path.home() / ".local" / "bin" / "uv", Path(sys.executable).parent / "uv"):
            if candidate.exists():
                return str(candidate)
        # Not present: install it beside the running interpreter. Deliberately not
        # the curl|sh installer — that needs network *and* a shell, and this path
        # already has a working Python.
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "uv"], check=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            raise RuntimeError(
                "uv is required to provision a workflow environment and could not be installed; "
                "install it from https://astral.sh/uv and retry (or pass --no-venv)"
            ) from exc
        installed = shutil.which("uv") or Path(sys.executable).parent / "uv"
        return str(installed)

    def _provision_venv(self, bundle_dir: Path) -> Path:
        """Create ``<bundle>/.venv`` with the bundle's requirements; return its python.

        A bundle already declares exactly what it needs in ``requirements.txt``;
        leaving an operator to satisfy that by hand is where a scheduled install
        goes wrong, because the failure lands hours later inside a timer with only
        a systemd status to explain it.

        uv fetches a matching CPython itself when the host has none, so this works
        on a bare box. Offline bundles carry their wheels under ``_vendor/wheels``
        and are installed from there.
        """
        uv = self._resolve_uv()
        wheels = bundle_dir / "_vendor" / "wheels"
        # The bundle ships a pyproject.toml, so it is an ordinary uv project:
        # `uv sync` reads requires-python, fetches a matching CPython when the host
        # has none, creates .venv, and installs the declared dependencies — one
        # command, one source of truth for the deps.
        cmd = [uv, "sync", "-q", "--project", str(bundle_dir)]
        if wheels.is_dir():  # offline bundle: install from its own vendored wheels
            cmd += ["--offline", "--find-links", str(wheels)]
        subprocess.run(cmd, check=True)
        return bundle_dir / ".venv" / "bin" / "python"

    def _install_timer(
        self, name: str, bundle_dir: Path, schedule: ScheduleDef, *, dry_run: bool, python: str | None = None
    ) -> None:
        units = render_timer_units(name, bundle_dir, schedule, python=python or self.python)
        for fname, text in units.files.items():
            self._write_unit(fname, text, dry_run=dry_run)
        self._run("daemon-reload", dry_run=dry_run)
        for unit in units.enable:
            self._run("enable", "--now", unit, dry_run=dry_run)
        self._register(name, {"mode": "timer", "bundle": str(bundle_dir), "units": list(units.files)}, dry_run=dry_run)

    def _install_hook(self, name: str, bundle_dir: Path, schedule: ScheduleDef, *, dry_run: bool) -> None:
        # Ensure the ONE shared listener service exists; add this workflow's route
        # to the registry (which the listener reads) and reload it.
        listener_unit = f"{LISTENER_SERVICE}.service"
        if not (self.unit_dir / listener_unit).exists():
            self._write_unit(
                listener_unit,
                render_listener_service(python=self.python, registry_path=self.registry_path),
                dry_run=dry_run,
            )
            self._run("daemon-reload", dry_run=dry_run)
            self._run("enable", "--now", listener_unit, dry_run=dry_run)
        self._register(
            name,
            {
                "mode": "hook",
                "bundle": str(bundle_dir),
                "signal": schedule.signal,
                "listen": schedule.listen,
                "units": [],  # served by the shared listener, no per-workflow unit
            },
            dry_run=dry_run,
        )
        # Reload the listener so it picks up the new route.
        self._run("reload-or-restart", listener_unit, dry_run=dry_run)

    def _write_unit(self, fname: str, text: str, *, dry_run: bool) -> None:
        if dry_run:
            print(f"\n# --- {fname} ---\n{text}")
            return
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        (self.unit_dir / fname).write_text(text, encoding="utf-8")

    def _register(self, name: str, entry: dict[str, object], *, dry_run: bool = False) -> None:
        if dry_run:
            return
        reg = self._load_registry()
        reg[name] = entry
        self._save_registry(reg)

    # -- list --------------------------------------------------------------
    def list_installed(self) -> list[dict[str, object]]:
        """Return the registry entries (name + mode + bundle + route)."""
        reg = self._load_registry()
        return [{"name": n, **e} for n, e in sorted(reg.items())]

    # -- delete ------------------------------------------------------------
    def delete(self, name: str, *, dry_run: bool = False) -> None:
        """Uninstall *name*: disable/remove its units, bundle, and registry entry."""
        reg = self._load_registry()
        if name not in reg:
            raise ValueError(f"no installed workflow named {name!r}")
        entry = reg[name]
        units = entry.get("units")
        if isinstance(units, list):
            for unit in units:
                if isinstance(unit, str) and unit.endswith((".timer", ".service")):
                    self._run("disable", "--now", unit, dry_run=dry_run)
            for unit in units:
                if isinstance(unit, str) and not dry_run:
                    (self.unit_dir / unit).unlink(missing_ok=True)
        # Remove the bundle dir.
        bundle = entry.get("bundle")
        if isinstance(bundle, str) and not dry_run:
            import shutil

            shutil.rmtree(bundle, ignore_errors=True)
        # Drop from the registry.
        del reg[name]
        if not dry_run:
            self._save_registry(reg)
        # If that was the last hook workflow, tear down the shared listener.
        if entry.get("mode") == "hook" and not any(e.get("mode") == "hook" for e in reg.values()):
            listener_unit = f"{LISTENER_SERVICE}.service"
            self._run("disable", "--now", listener_unit, dry_run=dry_run)
            if not dry_run:
                (self.unit_dir / listener_unit).unlink(missing_ok=True)
        elif entry.get("mode") == "hook":
            # Other hooks remain — reload the listener to drop this route.
            self._run("reload-or-restart", f"{LISTENER_SERVICE}.service", dry_run=dry_run)
