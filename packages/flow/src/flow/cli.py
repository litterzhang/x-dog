"""CLI entry point for xdog-flow.

Usage::

    xdog-flow validate <config.json>          Validate a workflow definition
    xdog-flow run <config.json> [--provider X] [--dry-run]
                                              Execute a workflow
    xdog-flow generate <config.json> -o OUT   Generate Python code
    xdog-flow graph <config.json> [--mermaid] Print workflow graph
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agent.core import StreamFn
from ai.types import AssistantMessage, DoneEvent, TextContent
from ai.utils.event_stream import EventStream as AiEventStream

from flow.codegen import generate
from flow.errors import WorkflowValidationError
from flow.executor import execute
from flow.graph import to_ascii, to_mermaid
from flow.loader import load_workflow

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


def _cmd_validate(config_path: str) -> None:
    """Load and validate a workflow; print OK or error."""
    try:
        wf = load_workflow(config_path)
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
) -> None:
    """Execute a workflow and print the final state as JSON."""
    try:
        wf = load_workflow(config_path)
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc))
        raise SystemExit(1)

    base_dir = Path(config_path).resolve().parent
    if dry_run:
        factory = _dry_run_stream_fn_factory
        result = await execute(wf, stream_fn_factory=factory, timeout=timeout, base_dir=base_dir)
    else:
        if provider is not None:
            import ai
            from agent.helpers import stream_fn_from_provider

            prov = ai.provider(provider)
            base_stream_fn = stream_fn_from_provider(prov)

            def _factory(model: str) -> StreamFn:
                return base_stream_fn

            result = await execute(wf, stream_fn_factory=_factory, timeout=timeout, base_dir=base_dir)
        else:
            result = await execute(wf, timeout=timeout, base_dir=base_dir)

    print(json.dumps(result.final_state, indent=2))


def _cmd_generate(config_path: str, *, output: str | None) -> None:
    """Generate a Python module from the workflow definition."""
    try:
        wf = load_workflow(config_path)
    except (WorkflowValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc))
        raise SystemExit(1)

    code = generate(wf)

    if output is None:
        print(code)
    else:
        Path(output).write_text(code, encoding="utf-8")


def _cmd_graph(config_path: str, *, mermaid: bool, svg: bool) -> None:
    """Print the workflow graph as ASCII, Mermaid, or SVG."""
    try:
        wf = load_workflow(config_path)
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
    val_p.add_argument("config", help="Path to workflow JSON file")

    # -- run -----------------------------------------------------------------
    run_p = sub.add_parser("run", help="Execute a workflow")
    run_p.add_argument("config", help="Path to workflow JSON file")
    run_p.add_argument("--provider", help="Override AI provider")
    run_p.add_argument("--dry-run", action="store_true", help="Inject stub LLM (offline)")
    run_p.add_argument(
        "--timeout", type=float, default=120.0, help="Per-node wall-clock timeout in seconds (default 120)"
    )

    # -- generate ------------------------------------------------------------
    gen_p = sub.add_parser("generate", help="Generate Python code from a workflow")
    gen_p.add_argument("config", help="Path to workflow JSON file")
    gen_p.add_argument("-o", "--output", help="Output file (default: stdout)")

    # -- graph ---------------------------------------------------------------
    graph_p = sub.add_parser("graph", help="Print workflow graph")
    graph_p.add_argument("config", help="Path to workflow JSON file")
    graph_p.add_argument("--mermaid", action="store_true", help="Output Mermaid format")
    graph_p.add_argument("--svg", action="store_true", help="Output SVG document (with embedded JSON)")

    # -- build ---------------------------------------------------------------
    build_p = sub.add_parser("build", help="Interactively build/edit a workflow (TUI)")
    build_p.add_argument("config", help="Path to workflow JSON file (created if missing)")

    args = parser.parse_args(argv)

    if args.command == "validate":
        _cmd_validate(args.config)
    elif args.command == "run":
        asyncio.run(_cmd_run(args.config, provider=args.provider, dry_run=args.dry_run, timeout=args.timeout))
    elif args.command == "generate":
        _cmd_generate(args.config, output=args.output)
    elif args.command == "graph":
        _cmd_graph(args.config, mermaid=args.mermaid, svg=args.svg)
    elif args.command == "build":
        _cmd_build(args.config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
