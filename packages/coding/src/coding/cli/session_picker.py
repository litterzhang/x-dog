"""Interactive session picker for resuming previous sessions."""

from __future__ import annotations

from datetime import datetime, timezone

from coding.core.session_manager import SessionManager, SessionMeta


def _format_session(meta: SessionMeta, idx: int) -> str:
    """Format one session line for display."""
    ts = datetime.fromtimestamp(meta.updated_at, tz=timezone.utc)
    time_str = ts.strftime("%Y-%m-%d %H:%M")
    summary = meta.summary[:60] if meta.summary else "(no summary)"
    return f"  {idx:>3d}. [{time_str}]  {meta.session_id[:8]}  {summary}"


def pick_session_command() -> str | None:
    """Show recent sessions and let the user pick one.

    Returns the chosen session ID or ``None`` if the user cancels.
    """
    manager = SessionManager()
    sessions = manager.list_sessions(limit=20)

    if not sessions:
        print("No sessions found.")
        return None

    print("\nRecent sessions:\n")
    for idx, meta in enumerate(sessions, 1):
        print(_format_session(meta, idx))
    print()

    while True:
        choice = input("Pick a session number (or 'q' to cancel): ").strip()
        if choice.lower() == "q":
            return None
        if choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(sessions):
                selected = sessions[num - 1]
                print(f"\nResuming session {selected.session_id[:8]}...")
                return selected.session_id
        print("Invalid selection, try again.")
