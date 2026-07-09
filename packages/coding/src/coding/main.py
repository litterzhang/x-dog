"""Main entry point for the coding agent CLI."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any


def main() -> None:
    """CLI entry point (referenced by pyproject.toml ``[project.scripts]``)."""
    from coding.cli.args import parse_args
    parse_args()


def run_agent(
    *,
    overrides: dict[str, Any] | None = None,
    resume: bool = False,
    resume_id: str | None = None,
    prompt: str | None = None,
    print_mode: bool = False,
    output_format: str = "text",
    working_dir: Path | None = None,
    config_path: Path | None = None,
    rpc: bool = False,
    verbose: bool = False,
    files: tuple[Path, ...] = (),
) -> None:
    """Bootstrap and run the coding agent.

    Called by :func:`coding.cli.args.cli` after argument parsing.
    """
    from coding.core.sdk import CreateSessionOptions, create_agent_session

    try:
        result = create_agent_session(CreateSessionOptions(
            working_dir=working_dir,
            overrides=overrides,
            resume=resume,
            resume_id=resume_id,
            verbose=verbose,
            config_path=config_path,
        ))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    session = result.session

    if result.model_fallback_message:
        print(result.model_fallback_message, file=sys.stderr)

    # Load extensions
    _load_extensions(session, verbose)

    # Build initial message from CLI args + piped stdin + files
    initial_message = _build_initial_message(prompt, files)

    # Route to the appropriate mode
    if rpc:
        _run_rpc(session)
    elif print_mode and initial_message:
        _run_print(session, initial_message, output_format, verbose)
    elif initial_message:
        # Non-interactive with prompt
        _run_print(session, initial_message, "text", verbose)
    else:
        _run_interactive(session, verbose)


def _build_initial_message(
    prompt: str | None,
    files: tuple[Path, ...],
) -> str | None:
    """Build initial message from CLI arguments."""
    from coding.cli.initial_message import build_initial_message
    return build_initial_message(
        prompt=prompt,
        files=files,
        read_stdin=True,
    )


def _load_extensions(session: Any, verbose: bool) -> None:
    """Discover and load configured extensions."""
    try:
        from coding.core.extensions.loader import discover_extensions

        extensions = discover_extensions()
        if verbose and extensions:
            print(f"Loaded {len(extensions)} extension(s):", file=sys.stderr)
            for ext in extensions:
                print(f"  - {ext.name} v{ext.manifest.version}", file=sys.stderr)
    except Exception as exc:
        if verbose:
            print(f"Warning: failed to load extensions: {exc}", file=sys.stderr)


def _run_print(
    session: Any,
    message: str,
    output_format: str,
    verbose: bool,
) -> None:
    """Run in non-interactive print mode."""
    from coding.modes.print_mode import run_print_mode

    exit_code = asyncio.run(
        run_print_mode(
            session,
            message,
            output_format=output_format,
            verbose=verbose,
        )
    )
    sys.exit(exit_code)


def _run_rpc(session: Any) -> None:
    """Run in headless RPC mode."""
    from coding.modes.rpc.rpc_mode import run_rpc_mode

    exit_code = asyncio.run(run_rpc_mode(session))
    sys.exit(exit_code)


def _run_interactive(session: Any, verbose: bool) -> None:
    """Run the interactive TUI mode."""
    try:
        from coding.modes.interactive.interactive_mode import run_interactive_mode
        run_interactive_mode(session, verbose=verbose)
    except ImportError:
        # Fallback to simple REPL if TUI is not available
        _run_simple_repl(session, verbose)


def _run_simple_repl(session: Any, verbose: bool) -> None:
    """Simple REPL fallback when the TUI is not available."""
    model_name = session.model.id if session.model else "unknown"
    thinking = session.agent.state.thinking_level or "off"

    print("Pi Coding Agent (interactive mode)")
    print(f"Model: {model_name} | Thinking: {thinking}")
    print(f"Working directory: {session.working_dir}")
    print("Type 'exit' or Ctrl+D to quit.\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            print("Goodbye.")
            break

        # Handle slash commands
        if user_input.startswith("/"):
            from coding.core.slash_commands import execute_command, parse_slash_command
            parsed = parse_slash_command(user_input)
            if parsed:
                cmd, args = parsed
                result = asyncio.run(execute_command(cmd, args, session))
                if result.output:
                    print(f"\n{result.output}\n")
                if result.exit_requested:
                    break
            continue

        # Send message
        try:
            response = asyncio.run(session.send_message(user_input))
            if response:
                from ai.types import TextContent
                text_parts = [
                    p.text for p in response.content
                    if isinstance(p, TextContent)
                ]
                print(f"\n{''.join(text_parts)}\n")
            else:
                print("\n(no response)\n")
        except KeyboardInterrupt:
            print("\nCancelled.")
            session.abort()
        except Exception as exc:
            print(f"\nError: {exc}\n", file=sys.stderr)
