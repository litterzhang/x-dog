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

from xdog.agent.core import StreamFn
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent
from xdog.ai.utils.event_stream import EventStream as AiEventStream
from xdog.flow.builder.io import load_any, parse_any
from xdog.flow.codegen import generate
from xdog.flow.errors import WorkflowPaused, WorkflowValidationError
from xdog.flow.events import FlowEvent, NodeFailed, NodeFinished, NodeStarted
from xdog.flow.executor import execute
from xdog.flow.graph import to_ascii, to_mermaid
from xdog.flow.loader import unconfinable_reasons, validation_errors
from xdog.flow.result import build_run_result

# ---------------------------------------------------------------------------
# Dry-run stub factory
# ---------------------------------------------------------------------------


def _dry_run_stream_fn_factory(model: str) -> StreamFn:
    """Return a StreamFn that echoes 'DRYRUN:<model>' without hitting an LLM."""

    def _stream_fn(
        model: str,
        context: Any,
        options: Any = None,
    ) -> AiEventStream[AssistantMessage]:
        text = f"DRYRUN:{model}"
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


_EVENT_LOG = logging.getLogger("xdog.flow.events")


def _log_event(event: FlowEvent) -> None:
    """Mirror a lifecycle event into ``-v`` output.

    Deliberately the same wording the generated module logs to
    ``flow.generated.events``: whichever engine ran it, the operator reading the
    journal should not have to learn two formats to answer the same question.
    """
    if not _EVENT_LOG.isEnabledFor(logging.INFO):
        return
    if isinstance(event, NodeStarted):
        _EVENT_LOG.info(
            "NodeStarted node=%s step=%d | %s",
            event.node_id, event.step, event.inputs_preview or "-",
        )
    elif isinstance(event, NodeFinished):
        _EVENT_LOG.info(
            "NodeFinished node=%s step=%d duration_s=%f | %s",
            event.node_id, event.step, event.duration_s, event.output_preview or "-",
        )
    elif isinstance(event, NodeFailed):
        _EVENT_LOG.info(
            "NodeFailed node=%s step=%d duration_s=%f error=%s",
            event.node_id, event.step, event.duration_s, event.error,
        )


def _stopped_by_for(exc: BaseException) -> dict[str, str] | None:
    """Classify a run-ending exception for the envelope's ``stoppedBy``.

    A strict ``while`` that never converged is a distinct, expected outcome —
    not an ordinary crash — so give it a machine-readable reason rather than
    leaving callers to pattern-match on the message text.
    """
    text = str(exc)
    if "did not converge within" in text:
        return {"reason": "loop_not_converged"}
    if isinstance(exc, WorkflowPaused):
        return {"reason": "paused", "node": exc.node_id, "signal": exc.signal}
    return None


def _cmd_validate(config_path: str, *, as_json: bool = False) -> None:
    """Load and validate a workflow; print OK or error."""
    if as_json:
        _cmd_validate_json(config_path)
        return
    try:
        wf = load_any(config_path)
        print(f"OK: {wf.name}")
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc))
        raise SystemExit(1) from None


def _cmd_validate_json(config_path: str) -> None:
    """Emit every validation problem as one JSON envelope; exit 1 if any.

    The prose form stops at the first failure, so an authoring Agent needs one
    round trip per mistake.  This reports the whole per-node and per-edge pass at
    once, each error carrying the node or edge it belongs to.

    A read or parse failure is still a single error — there is no graph yet to
    say anything more about.
    """
    envelope: dict[str, object] = {"ok": False, "path": config_path, "workflow": "", "errors": []}
    try:
        wf = parse_any(config_path)
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        detail = exc.as_dict() if isinstance(exc, WorkflowValidationError) else {"message": str(exc)}
        envelope["errors"] = [detail]
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
        raise SystemExit(1) from None

    errors = validation_errors(wf)
    envelope["workflow"] = wf.name
    envelope["ok"] = not errors
    envelope["errors"] = [exc.as_dict() for exc in errors]
    print(json.dumps(envelope, indent=2, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


async def _cmd_run(
    config_path: str,
    *,
    provider: str | None,
    dry_run: bool,
    timeout: float = 120.0,
    inputs: dict[str, object] | None = None,
    confined: bool = False,
    workspace: str | None = None,
    allow_paths: list[str] | None = None,
) -> None:
    """Execute a workflow and print a stable structured result envelope."""
    start_time = time.time()
    workflow_name = Path(config_path).stem
    # A failing run never returns an ExecResult, so the trace it would have carried
    # is gone by the time we build the envelope. Track the two fields that matter
    # off the event stream instead: without this the failure path reported
    # lastNode="" and tokensUsed=0 while the generated module reported the truth —
    # a divergence in a documented contract, in exactly the case you need it.
    last_node = ""
    tokens_seen = 0

    def _track(event: FlowEvent) -> None:
        nonlocal last_node, tokens_seen
        if isinstance(event, NodeFinished):
            last_node = event.node_id
            tokens_seen += event.tokens
        _log_event(event)

    try:
        wf = load_any(config_path)
        workflow_name = wf.name
        if inputs:
            logging.getLogger("flow").debug("run inputs override $in: %s", inputs)

        base_dir = Path(config_path).resolve().parent

        confine_kwargs: dict[str, object] = {}
        if confined:
            reasons = unconfinable_reasons(wf)
            if reasons:
                # Refusing is what makes the flag honest. Running anyway would
                # confine nothing while looking like it had.
                print(f"error: {wf.name!r} cannot be confined:")
                for reason in reasons:
                    print(f"  - {reason}")
                raise SystemExit(2)
            confine_kwargs = {
                "workspace": Path(workspace).resolve() if workspace else base_dir / "runtime",
                "allow_paths": [Path(p).resolve() for p in (allow_paths or ())],
            }

        if dry_run:
            result = await execute(
                wf,
                stream_fn_factory=_dry_run_stream_fn_factory,
                timeout=timeout,
                base_dir=base_dir,
                inputs=inputs,
                on_event=_track,
                **confine_kwargs,  # type: ignore[arg-type]
            )
        elif provider is not None:
            import xdog.ai as ai
            from xdog.agent.helpers import stream_fn_from_provider

            base_stream_fn = stream_fn_from_provider(ai.provider(provider))

            def _factory(model: str) -> StreamFn:
                return base_stream_fn

            result = await execute(
                wf,
                stream_fn_factory=_factory,
                timeout=timeout,
                base_dir=base_dir,
                inputs=inputs,
                on_event=_track,
                **confine_kwargs,  # type: ignore[arg-type]
            )
        else:
            result = await execute(
                wf,
                timeout=timeout,
                base_dir=base_dir,
                inputs=inputs,
                on_event=_track,
                **confine_kwargs,  # type: ignore[arg-type]
            )
    except Exception as exc:
        envelope = build_run_result(
            success=False,
            message=str(exc) or type(exc).__name__,
            output={},
            workflow=workflow_name,
            run_id=None,
            start_time=start_time,
            end_time=time.time(),
            tokens_used=tokens_seen,
            last_node=last_node,
            stopped_by=_stopped_by_for(exc),
        )
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
        raise SystemExit(1)

    runtime = result.runtime
    context = runtime["ctx"]
    stopped_by = runtime.get("stopped_by")
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
        stopped_by=stopped_by if isinstance(stopped_by, dict) else None,
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
        from xdog.flow.bundle import build_bundle

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
        from xdog.flow.builder.svg_doc import workflow_to_svg_document

        print(workflow_to_svg_document(wf))
    elif mermaid:
        print(to_mermaid(wf))
    else:
        print(to_ascii(wf))


def _cmd_build(config_path: str) -> None:
    """Open the interactive TUI workflow builder on *config_path*."""
    from xdog.flow.builder.app import run as run_builder

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
    from xdog.flow.testing import discover, load_suite, run_case
    from xdog.flow.testing.report import render_suite, render_total

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
    from xdog.flow.scheduler.install import Installer, default_data_dir, default_unit_dir

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
    no_venv: bool = False,
) -> None:
    """Install one scheduled workflow."""
    try:
        wf = load_any(config_path)
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc))
        raise SystemExit(1)
    try:
        installed = _scheduling_installer(python).install(
            wf,
            name=name,
            dry_run=dry_run,
            base_dir=Path(config_path).resolve().parent,
            venv=not no_venv,
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
    val_p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Report every problem as one JSON envelope (for tools and Agents)",
    )

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
        "--confined",
        action="store_true",
        help=(
            "Bound filesystem access to a workspace (default <workflow dir>/runtime). "
            "Refuses workflows containing an inline 'code' script, the 'bash' tool, "
            "or a CLI backend, since none of those can be confined cooperatively."
        ),
    )
    run_p.add_argument(
        "--workspace",
        metavar="DIR",
        help="Workspace directory for --confined (default: <workflow dir>/runtime)",
    )
    run_p.add_argument(
        "--allow-path",
        action="append",
        default=[],
        metavar="DIR",
        help=(
            "Grant --confined access to another directory (repeatable). "
            "Deliberately a run-time flag: a workflow that could declare its own "
            "access would not be confined by it."
        ),
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
        "--no-venv",
        action="store_true",
        help=(
            "Do not give the bundle its own virtualenv; run it with --python instead "
            "(that interpreter must already satisfy the bundle's requirements.txt)"
        ),
    )
    scheduling_install.add_argument(
        "--python",
        help="Interpreter for the unit's ExecStart when --no-venv is used (default: /usr/bin/python3)",
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
        _cmd_validate(args.config, as_json=args.as_json)
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
                confined=args.confined,
                workspace=args.workspace,
                allow_paths=args.allow_path,
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
                args.config,
                name=args.name,
                dry_run=args.dry_run,
                python=args.python,
                no_venv=args.no_venv,
            )
        elif args.scheduling_command == "uninstall":
            _cmd_scheduling_uninstall(args.name, dry_run=args.dry_run)
        elif args.scheduling_command == "list":
            _cmd_scheduling_list()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
