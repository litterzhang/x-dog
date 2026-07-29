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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
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


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    """Parse ``--input K=V`` flags into a dict; split on the first ``=`` only.

    Values may themselves contain ``=`` (e.g. ``note=x=y``).  A pair without any
    ``=`` is a usage error.
    """
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            print(f"error: --input expects K=V, got {pair!r}")
            raise SystemExit(2)
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
    inputs: dict[str, str] | None = None,
) -> None:
    """Execute a workflow and print its outputs ($output) as JSON."""
    try:
        wf = load_any(config_path)
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc))
        raise SystemExit(1)

    if inputs:
        logging.getLogger("flow").debug("run inputs override $in: %s", inputs)

    base_dir = Path(config_path).resolve().parent
    if dry_run:
        factory = _dry_run_stream_fn_factory
        result = await execute(wf, stream_fn_factory=factory, timeout=timeout, base_dir=base_dir, inputs=inputs)
    else:
        if provider is not None:
            import ai
            from agent.helpers import stream_fn_from_provider

            prov = ai.provider(provider)
            base_stream_fn = stream_fn_from_provider(prov)

            def _factory(model: str) -> StreamFn:
                return base_stream_fn

            result = await execute(
                wf, stream_fn_factory=_factory, timeout=timeout, base_dir=base_dir, inputs=inputs
            )
        else:
            result = await execute(wf, timeout=timeout, base_dir=base_dir, inputs=inputs)

    # By default show the workflow's declared outputs ($output); when a workflow
    # declares none, fall back to the full runtime container for debugging.
    rt = result.runtime
    print(json.dumps(rt["out"] if rt["out"] else rt, indent=2, ensure_ascii=False))


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
            out_dir = build_bundle(wf, Path(output), offline=offline)
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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
