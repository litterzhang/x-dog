"""flow.scheduler.listener — the ONE shared hook listener (docs/scheduling.md §5).

A single process serves every installed **hook** workflow: it reads the install
registry, and on each external event delivers the workflow's ``signal`` to a fresh
run of its bundle (``python <bundle>`` with ``FLOW_SIGNALS`` + ``FLOW_INPUTS`` in
the env).  The workflow's ``human`` node with that signal then proceeds instead of
pausing — the passive path reuses the existing pause/resume primitive verbatim.

Transports (from each entry's ``listen`` block):
- **http**: one bound port; the request ``path`` routes to the workflow, the JSON
  body becomes ``FLOW_INPUTS``.
- **file**: a watched directory per workflow; a dropped file's content is the input.

Routing + spawning are separated from the transports so tests drive
:class:`Router` in-process against a fake spawn (no real subprocess, port, or
watcher).  systemd owns the process lifecycle (§5.1); this module is only the
event→signal adapter.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# A spawn function: (bundle_dir, env) -> None.  Injectable for tests.
SpawnFn = Callable[[Path, dict[str, str]], None]


def _default_spawn(bundle_dir: Path, env: dict[str, str]) -> None:
    """Run the bundle as a detached subprocess (fire-and-forget)."""
    subprocess.Popen(  # noqa: S603
        ["/usr/bin/python3", str(bundle_dir)],
        env={**os.environ, **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@dataclass(frozen=True)
class HookRoute:
    """One installed hook workflow's routing info (from the registry)."""

    name: str
    bundle: Path
    signal: str
    listen: dict[str, object]


@dataclass
class Router:
    """Routes events to bundle runs for every installed hook workflow.

    Built from the install registry.  ``deliver_http(path, body)`` /
    ``deliver_file(dir, content)`` find the matching route and spawn its bundle with
    ``FLOW_SIGNALS`` + ``FLOW_INPUTS``.  The spawn is injectable so tests observe the
    env without running a subprocess.
    """

    routes: list[HookRoute]
    spawn: SpawnFn = _default_spawn
    _by_http_path: dict[str, HookRoute] = field(default_factory=dict, init=False)
    _by_file_dir: dict[str, HookRoute] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        for r in self.routes:
            t = r.listen.get("type")
            if t == "http":
                path = r.listen.get("path")
                if isinstance(path, str):
                    self._by_http_path[path] = r
            elif t == "file":
                d = r.listen.get("dir")
                if isinstance(d, str):
                    self._by_file_dir[str(Path(d))] = r

    @classmethod
    def from_registry(cls, registry_path: Path, *, spawn: SpawnFn = _default_spawn) -> Router:
        reg: dict[str, dict[str, object]] = {}
        if registry_path.exists():
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                reg = data
        routes: list[HookRoute] = []
        for name, e in reg.items():
            if e.get("mode") != "hook" or "bundle" not in e:
                continue
            listen = e.get("listen")
            routes.append(
                HookRoute(
                    name=name,
                    bundle=Path(str(e["bundle"])),
                    signal=str(e.get("signal", "")),
                    listen=listen if isinstance(listen, dict) else {},
                )
            )
        return cls(routes=routes, spawn=spawn)

    def _fire(self, route: HookRoute, inputs: dict[str, object]) -> None:
        env = {"FLOW_SIGNALS": route.signal, "FLOW_RUN_ID": route.name}
        if inputs:
            env["FLOW_INPUTS"] = json.dumps(inputs, sort_keys=True)
        self.spawn(route.bundle, env)

    def deliver_http(self, path: str, body: bytes) -> HookRoute:
        """Route an HTTP event by request path; the JSON body is the input."""
        route = self._by_http_path.get(path)
        if route is None:
            raise KeyError(f"no hook workflow bound to path {path!r}")
        inputs = json.loads(body) if body.strip() else {}
        if not isinstance(inputs, dict):
            raise ValueError("hook body must be a JSON object")
        self._fire(route, inputs)
        return route

    def deliver_file(self, directory: str, content: bytes) -> HookRoute:
        """Route a file-drop event by watched dir; the file content is the input."""
        route = self._by_file_dir.get(str(Path(directory)))
        if route is None:
            raise KeyError(f"no hook workflow watching {directory!r}")
        inputs = json.loads(content) if content.strip() else {}
        if not isinstance(inputs, dict):
            raise ValueError("dropped file must contain a JSON object")
        self._fire(route, inputs)
        return route

    def http_paths(self) -> list[str]:
        return sorted(self._by_http_path)


def _serve_http(router: Router, port: int) -> None:  # pragma: no cover - real server
    """Bind *port* and route each POST through *router* (blocking)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            try:
                route = router.deliver_http(self.path, body)
            except KeyError:
                self.send_response(404)
                self.end_headers()
                return
            except (ValueError, json.JSONDecodeError):
                self.send_response(400)
                self.end_headers()
                return
            self.send_response(202)
            self.end_headers()
            self.wfile.write(f"accepted: {route.name}\n".encode())

        def log_message(self, *_: object) -> None:
            pass  # quiet

    ThreadingHTTPServer(("127.0.0.1", port), _Handler).serve_forever()


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - entry point
    parser = argparse.ArgumentParser(prog="flow-listener")
    parser.add_argument("--registry", required=True, help="Path to the install registry JSON")
    parser.add_argument("--port", type=int, default=8787, help="HTTP port to bind (default 8787)")
    args = parser.parse_args(argv)
    router = Router.from_registry(Path(args.registry))
    _serve_http(router, args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
