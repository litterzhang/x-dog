"""Transcript store — pure JSONL persistence for session transcripts.

Handles session metadata (sessions.json index), transcript files
({session_id}.jsonl), and conversation branching. No knowledge of
AgentSession or GroupRuntime — those are lifecycle concerns handled
by GroupRuntime.
"""
from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path
from typing import Any

from xdog.claw.core.types import SessionMeta


class TranscriptStore:
    """Pure JSONL persistence for session transcripts."""

    def __init__(self, sessions_dir: Path):
        self._sessions_dir = Path(sessions_dir)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self._sessions_dir / "sessions.json"
        self._index: dict[str, dict[str, Any]] = self._load_index()

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self._index_file.exists():
            try:
                with open(self._index_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return {}

    def _save_index(self) -> None:
        with open(self._index_file, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2)

    def create_session(self, group_id: str) -> SessionMeta:
        """Create a new session with uuid-based ID."""
        session_id = str(uuid.uuid4())
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        meta = SessionMeta(
            session_id=session_id,
            group_id=group_id,
            created_at=now,
            last_active=now,
            turn_count=0,
            label=""
        )

        self._index[group_id] = {
            "session_id": meta.session_id,
            "group_id": meta.group_id,
            "created_at": meta.created_at,
            "last_active": meta.last_active,
            "turn_count": meta.turn_count,
            "label": meta.label
        }
        self._save_index()

        transcript_file = self._sessions_dir / f"{session_id}.jsonl"
        transcript_file.touch(exist_ok=True)

        return meta

    def get_active_session(self, group_id: str) -> SessionMeta | None:
        """Get the active session for a group ID."""
        if group_id in self._index:
            data = self._index[group_id]
            return SessionMeta(
                session_id=data["session_id"],
                group_id=data["group_id"],
                created_at=data["created_at"],
                last_active=data["last_active"],
                turn_count=data.get("turn_count", 0),
                label=data.get("label", "")
            )
        return None

    def reset_session(self, group_id: str) -> SessionMeta:
        """Archive current session, start a new one."""
        return self.create_session(group_id)

    def append_turn(self, session_id: str, turn: dict[str, Any]) -> None:
        """Append a turn to the JSONL file."""
        transcript_file = self._sessions_dir / f"{session_id}.jsonl"
        with open(transcript_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(turn) + "\n")

    def load_transcript(self, session_id: str) -> list[dict]:
        """Load all turns from a session."""
        transcript_file = self._sessions_dir / f"{session_id}.jsonl"
        if not transcript_file.exists():
            return []

        turns = []
        with open(transcript_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    turns.append(json.loads(line))
        return turns

    def increment_turn(self, session_id: str) -> SessionMeta:
        """Bump turn count + last_active."""
        group_id = None
        for gid, data in self._index.items():
            if data["session_id"] == session_id:
                group_id = gid
                break

        if not group_id:
            raise ValueError(f"Session {session_id} not found in index")

        data = self._index[group_id]
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        meta = SessionMeta(
            session_id=data["session_id"],
            group_id=data["group_id"],
            created_at=data["created_at"],
            last_active=now,
            turn_count=data.get("turn_count", 0) + 1,
            label=data.get("label", "")
        )

        self._index[group_id] = {
            "session_id": meta.session_id,
            "group_id": meta.group_id,
            "created_at": meta.created_at,
            "last_active": meta.last_active,
            "turn_count": meta.turn_count,
            "label": meta.label
        }
        self._save_index()

        return meta

    def needs_daily_reset(self, group_id: str, reset_hour: int = 4) -> bool:
        """True if the session was created before today's reset hour."""
        meta = self.get_active_session(group_id)
        if meta is None or not meta.created_at:
            return False
        try:
            created = datetime.datetime.fromisoformat(meta.created_at)
        except (ValueError, TypeError):
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        today_reset = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
        if now < today_reset:
            today_reset -= datetime.timedelta(days=1)
        return created < today_reset

    def needs_idle_reset(self, group_id: str, idle_seconds: int) -> bool:
        """True if the session has been idle longer than *idle_seconds*."""
        if idle_seconds <= 0:
            return False
        meta = self.get_active_session(group_id)
        if meta is None or not meta.last_active:
            return False
        try:
            last = datetime.datetime.fromisoformat(meta.last_active)
        except (ValueError, TypeError):
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        elapsed = (now - last).total_seconds()
        return elapsed >= idle_seconds

    def replace_transcript(self, session_id: str, turns: list[dict]) -> None:
        """Replace the entire transcript for a session (used after compaction)."""
        transcript_file = self._sessions_dir / f"{session_id}.jsonl"
        with open(transcript_file, "w", encoding="utf-8") as f:
            for turn in turns:
                f.write(json.dumps(turn) + "\n")

    # -- Branching ---------------------------------------------------------------

    def save_branch(self, session_id: str, branch_id: str, transcript: list[dict]) -> None:
        """Save a conversation branch as a JSONL snapshot."""
        branch_file = self._sessions_dir / f"{session_id}_branch_{branch_id}.jsonl"
        with open(branch_file, "w", encoding="utf-8") as f:
            for turn in transcript:
                f.write(json.dumps(turn) + "\n")

    def load_branch(self, session_id: str, branch_id: str) -> list[dict] | None:
        """Load a conversation branch. Returns None if not found."""
        branch_file = self._sessions_dir / f"{session_id}_branch_{branch_id}.jsonl"
        if not branch_file.exists():
            return None
        turns = []
        with open(branch_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    turns.append(json.loads(line))
        return turns

    def list_branches(self, session_id: str) -> list[str]:
        """List all branch IDs for a session."""
        prefix = f"{session_id}_branch_"
        branches = []
        for path in self._sessions_dir.glob(f"{prefix}*.jsonl"):
            branch_id = path.stem.removeprefix(prefix)
            branches.append(branch_id)
        return sorted(branches)


# Backward compatibility alias
SessionManager = TranscriptStore
