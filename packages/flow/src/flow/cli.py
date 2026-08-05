"""CLI entry point for xdog-flow.

Every command accepts either a ``.json`` workflow or a ``.svg`` document that
embeds the workflow JSON (see ``xdog-flow graph <wf> --svg``).

Usage::

    xdog-flow validate <config.json|.svg>     Validate a workflow definition
    xdog-flow run <config.json|.svg> [--provider X] [--dry-run] [--input K=V ...]
                                              Execute a workflow
    xdog-flow generate <config.json|.svg> -o OUT   Generate Python code
    xdog-flow generate <config> --portable -o DIR  Emit a self-contained bundle
    xdog-flow graph <config.json|.svg> [--mermaid|--svg]  Print workflow graph
    xdog-flow scheduling install <config.json|.svg>     Install a schedule
    xdog-flow scheduling uninstall <name>               Uninstall a schedule
    xdog-flow scheduling list                           List schedules
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from agent.core import StreamFn
from ai.types import AssistantMessage, DoneEvent, TextContent
from ai.utils.event_stream import EventStream as AiEventStream

from flow.builder.io import load_any
from flow.codegen import generate
from flow.errors import WorkflowValidationError
from flow.executor import execute
from flow.graph import to_ascii, to_mermaid
from flow.result import build_run_result

# ---------------------------------------------------------------------------
# Dry-run stub factory
# ---------------------------------------------------------------------------


def _dry_run_stream_fn_factory(model: str) -> StreamFn:
    """Return a StreamFn that echoes 'DRYRUN:<model>' without hitting an LLM."""

    def _stream_fn(
        model_id: str,
        context: Any,
        options: Any = None,
    ) -> AiEventStream[AssistantMessage]:
        text = f"DRYRUN:{model_id}"
        msg = AssistantMessage(content=(TextContent(text=text),))
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason="stop", message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    return _stream_fn


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _parse_inputs(pairs: list[str]) -> dict[str, object]:
    """Parse ``--input K=V`` flags into a dict; split on the first ``=`` only.

    Values may themselves contain ``=`` (e.g. ``note=x=y``).  A pair without any
    ``=`` is a usage error.  Each value is parsed as JSON when possible so a
    structured seed (``items=[1,2]``, ``cfg={"a":1}``, ``n=5``) is expressible;
    a value that is not valid JSON (a bare word) is kept as the raw string.
    """
    out: dict[str, object] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            print(f"error: --input expects K=V, got {pair!r}")
            raise SystemExit(2)
        try:
            out[key] = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            out[key] = value
    return out


def _cmd_validate(config_path: str) -> None:
    """Load and validate a workflow; print OK or error."""
    try:
        wf = load_any(config_path)
        print(f"OK: {wf.name}")
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc))
        raise SystemExit(1)


async def _cmd_run(
    config_path: str,
    *,
    provider: str | None,
    dry_run: bool,
    timeout: float = 120.0,
    inputs: dict[str, object] | None = None,
) -> None:
    """Execute a workflow and print a stable structured result envelope."""
    start_time = time.time()
    workflow_name = Path(config_path).stem
    try:
        wf = load_any(config_path)
        workflow_name = wf.name
        if inputs:
            logging.getLogger("flow").debug("run inputs override $in: %s", inputs)

        base_dir = Path(config_path).resolve().parent
        if dry_run:
            result = await execute(
                wf,
                stream_fn_factory=_dry_run_stream_fn_factory,
                timeout=timeout,
                base_dir=base_dir,
                inputs=inputs,
            )
        elif provider is not None:
            import ai
            from agent.helpers import stream_fn_from_provider

            base_stream_fn = stream_fn_from_provider(ai.provider(provider))

            def _factory(model: str) -> StreamFn:
                return base_stream_fn

            result = await execute(
                wf, stream_fn_factory=_factory, timeout=timeout, base_dir=base_dir, inputs=inputs
            )
        else:
            result = await execute(wf, timeout=timeout, base_dir=base_dir, inputs=inputs)
    except Exception as exc:
        envelope = build_run_result(
            success=False,
            message=str(exc) or type(exc).__name__,
            output={},
            workflow=workflow_name,
            run_id=None,
            start_time=start_time,
            end_time=time.time(),
            tokens_used=0,
            last_node="",
        )
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
        raise SystemExit(1)

    runtime = result.runtime
    context = runtime["ctx"]
    envelope = build_run_result(
        success=True,
        message="Workflow completed",
        output=dict(runtime["out"]),
        workflow=workflow_name,
        run_id=None,
        start_time=start_time,
        end_time=time.time(),
        tokens_used=int(runtime["tokens_used"]),
        last_node=str(context["node_id"]),
    )
    print(json.dumps(envelope, indent=2, ensure_ascii=False))


def _cmd_generate(config_path: str, *, output: str | None, portable: bool = False, offline: bool = False) -> None:
    """Generate a Python module (or a portable bundle) from the workflow definition."""
    try:
        wf = load_any(config_path)
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc))
        raise SystemExit(1)

    if portable:
        if output is None:
            print("--portable requires -o/--output (the bundle directory)")
            raise SystemExit(1)
        from flow.bundle import build_bundle

        try:
            out_dir = build_bundle(
                wf, Path(output), base_dir=Path(config_path).resolve().parent, offline=offline
            )
        except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            print(str(exc))
            raise SystemExit(1)
        print(f"Bundle written to {out_dir}")
        return

    code = generate(wf)

    if output is None:
        print(code)
    else:
        Path(output).write_text(code, encoding="utf-8")


def _cmd_graph(config_path: str, *, mermaid: bool, svg: bool) -> None:
    """Print the workflow graph as ASCII, Mermaid, or SVG."""
    try:
        wf = load_any(config_path)
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc))
        raise SystemExit(1)

    if svg:
        from flow.builder.svg_doc import workflow_to_svg_document

        print(workflow_to_svg_document(wf))
    elif mermaid:
        print(to_mermaid(wf))
    else:
        print(to_ascii(wf))


def _cmd_build(config_path: str) -> None:
    """Open the interactive TUI workflow builder on *config_path*."""
    from flow.builder.app import run as run_builder

    run_builder(config_path)


def _cmd_test(
    target: str,
    *,
    case_name: str | None,
    allow_script_stub: bool,
    verbose: bool,
) -> None:
    """Run the companion ``*.test.json`` suite(s) for *target*.

    *target* may be a workflow (``foo.json`` finds ``foo.test.json``), a suite file,
    or a directory to sweep.  Exits 1 if any case fails, so this drops straight into
    a pre-commit hook or CI step.
    """
    from flow.testing import discover, load_suite, run_case
    from flow.testing.report import render_suite, render_total

    try:
        suite_files = discover(Path(target))
    except WorkflowValidationError as exc:
        print(str(exc))
        raise SystemExit(1)
    if not suite_files:
        print(f"no *.test.json suites under {target}")
        raise SystemExit(1)

    results = []
    for suite_file in suite_files:
        try:
            suite, wf = load_suite(suite_file, allow_script_stub=allow_script_stub)
        except (WorkflowValidationError, FileNotFoundError) as exc:
            print(f"{suite_file}: {exc}")
            raise SystemExit(1)

        selected = [c for c in suite.cases if case_name is None or c.name == case_name]
        if case_name is not None and not selected:
            known = ", ".join(repr(c.name) for c in suite.cases)
            print(f"{suite_file}: no case named {case_name!r}; cases are: {known}")
            raise SystemExit(1)

        suite_results = [run_case(wf, c, base_dir=suite.workflow_path.parent) for c in selected]
        results.extend(suite_results)
        for line in render_suite(str(suite_file), suite_results, verbose=verbose):
            print(line)

    print("")
    print(render_total(results))
    if any(not r.ok for r in results):
        raise SystemExit(1)


def _scheduling_installer(python: str | None = None) -> Any:
    from flow.scheduler.install import Installer, default_data_dir, default_unit_dir

    kwargs: dict[str, Any] = {"unit_dir": default_unit_dir(), "data_dir": default_data_dir()}
    if python:
        kwargs["python"] = python
    return Installer(**kwargs)


def _cmd_scheduling_install(
    config_path: str,
    *,
    name: str | None,
    dry_run: bool,
    python: str | None = None,
) -> None:
    """Install one scheduled workflow."""
    try:
        wf = load_any(config_path)
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc))
        raise SystemExit(1)
    try:
        installed = _scheduling_installer(python).install(
            wf, name=name, dry_run=dry_run, base_dir=Path(config_path).resolve().parent
        )
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(1)
    print(f"Installed {installed}" if not dry_run else f"(dry-run) would install {installed}")


def _cmd_scheduling_uninstall(name: str, *, dry_run: bool) -> None:
    """Uninstall one scheduled workflow."""
    try:
        _scheduling_installer().delete(name, dry_run=dry_run)
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(1)
    print(f"Uninstalled {name}" if not dry_run else f"(dry-run) would uninstall {name}")


def _cmd_scheduling_list() -> None:
    """List installed scheduled workflows."""
    rows = _scheduling_installer().list_installed()
    if not rows:
        print("(no scheduled workflows installed)")
        return
    for entry in rows:
        extra = entry.get("signal") if entry.get("mode") == "hook" else ""
        print(f"{entry['name']:24} {entry['mode']:6} {entry.get('bundle', '')}  {extra}".rstrip())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``xdog-flow`` console script."""
    parser = argparse.ArgumentParser(
        prog="xdog-flow",
        description="xdog-flow: workflow engine and code generator.",
    )
    sub = parser.add_subparsers(dest="command")

    # -- validate ------------------------------------------------------------
    val_p = sub.add_parser("validate", help="Validate a workflow definition")
    val_p.add_argument("config", help="Path to workflow .json or .svg file")

    # -- run -----------------------------------------------------------------
    run_p = sub.add_parser("run", help="Execute a workflow")
    run_p.add_argument("config", help="Path to workflow .json or .svg file")
    run_p.add_argument("--provider", help="Override AI provider")
    run_p.add_argument("--dry-run", action="store_true", help="Inject stub LLM (offline)")
    run_p.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="K=V",
        help="Seed/override a $in input port (repeatable), e.g. --input a=3",
    )
    run_p.add_argument(
        "--timeout", type=float, default=120.0, help="Per-node wall-clock timeout in seconds (default 120)"
    )
    run_p.add_argument(
        "-v", "--verbose", action="store_true", help="Show DEBUG logs (node execution, loop firing)"
    )

    # -- generate ------------------------------------------------------------
    gen_p = sub.add_parser("generate", help="Generate Python code from a workflow")
    gen_p.add_argument("config", help="Path to workflow .json or .svg file")
    gen_p.add_argument("-o", "--output", help="Output file, or bundle dir with --portable (default: stdout)")
    gen_p.add_argument(
        "--portable",
        action="store_true",
        help="Emit a self-contained bundle dir (vendors ai/agent) instead of a single module",
    )
    gen_p.add_argument(
        "--offline",
        action="store_true",
        help="With --portable: also download third-party wheels for a no-network install",
    )

    # -- graph ---------------------------------------------------------------
    graph_p = sub.add_parser("graph", help="Print workflow graph")
    graph_p.add_argument("config", help="Path to workflow .json or .svg file")
    graph_p.add_argument("--mermaid", action="store_true", help="Output Mermaid format")
    graph_p.add_argument("--svg", action="store_true", help="Output SVG document (with embedded JSON)")

    # -- build ---------------------------------------------------------------
    build_p = sub.add_parser("build", help="Interactively build/edit a workflow (TUI)")
    build_p.add_argument("config", help="Path to workflow JSON file (created if missing)")

    # -- test ----------------------------------------------------------------
    test_p = sub.add_parser("test", help="Run a workflow's companion *.test.json suite")
    test_p.add_argument(
        "target",
        help="Workflow .json (finds the sibling .test.json), a .test.json, or a directory",
    )
    test_p.add_argument("--case", help="Run only the case with this name")
    test_p.add_argument(
        "--allow-script-stub",
        action="store_true",
        help="Permit 'scripts' stubs (script nodes run for real by default)",
    )
    test_p.add_argument("-v", "--verbose", action="store_true", help="Show the node trace for passing cases too")

    # -- scheduling ---------------------------------------------------------
    scheduling_p = sub.add_parser("scheduling", help="Manage scheduled workflows")
    scheduling_sub = scheduling_p.add_subparsers(dest="scheduling_command", required=True)

    scheduling_install = scheduling_sub.add_parser("install", help="Install a scheduled workflow")
    scheduling_install.add_argument("config", help="Path to workflow .json/.svg")
    scheduling_install.add_argument("--name", help="Install name (default: the workflow name)")
    scheduling_install.add_argument(
        "--python",
        help=(
            "Interpreter for the unit's ExecStart (default: /usr/bin/python3). "
            "Point this at an environment that has the bundle's requirements.txt installed"
        ),
    )
    scheduling_install.add_argument(
        "--dry-run", action="store_true", help="Print units/actions without touching the OS"
    )

    scheduling_uninstall = scheduling_sub.add_parser("uninstall", help="Uninstall a scheduled workflow")
    scheduling_uninstall.add_argument("name", help="Installed workflow name")
    scheduling_uninstall.add_argument(
        "--dry-run", action="store_true", help="Print units/actions without touching the OS"
    )

    scheduling_sub.add_parser("list", help="List installed scheduled workflows")

    args = parser.parse_args(argv)

    if args.command == "validate":
        _cmd_validate(args.config)
    elif args.command == "run":
        if args.verbose:
            logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
            # Surface flow's own DEBUG logs (node execution, loop firing) without
            # the noise of third-party libraries (asyncio, openai, httpx, …).
            logging.getLogger("flow").setLevel(logging.DEBUG)
        asyncio.run(
            _cmd_run(
                args.config,
                provider=args.provider,
                dry_run=args.dry_run,
                timeout=args.timeout,
                inputs=_parse_inputs(args.input),
            )
        )
    elif args.command == "generate":
        _cmd_generate(args.config, output=args.output, portable=args.portable, offline=args.offline)
    elif args.command == "graph":
        _cmd_graph(args.config, mermaid=args.mermaid, svg=args.svg)
    elif args.command == "build":
        _cmd_build(args.config)
    elif args.command == "test":
        _cmd_test(
            args.target,
            case_name=args.case,
            allow_script_stub=args.allow_script_stub,
            verbose=args.verbose,
        )
    elif args.command == "scheduling":
        if args.scheduling_command == "install":
            _cmd_scheduling_install(
                args.config, name=args.name, dry_run=args.dry_run, python=args.python
            )
        elif args.scheduling_command == "uninstall":
            _cmd_scheduling_uninstall(args.name, dry_run=args.dry_run)
        elif args.scheduling_command == "list":
            _cmd_scheduling_list()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
