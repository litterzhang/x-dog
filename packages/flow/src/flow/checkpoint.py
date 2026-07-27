"""flow.checkpoint — checkpoint store protocol and JSON-file implementation."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol


class CheckpointStore(Protocol):
    """Persists and restores a run's progress snapshot, keyed by run_id."""

    def save(self, run_id: str, snapshot: dict[str, Any]) -> None: ...

    def load(self, run_id: str) -> dict[str, Any] | None: ...  # None if absent


class JSONFileCheckpointStore:
    """Checkpoint store backed by JSON files in a directory.

    Each run_id maps to ``<dir>/<run_id>.json``.  Writes are atomic: the
    snapshot is first written to a temp file in the same directory and then
    renamed over the target so a crash during the write never leaves a
    partially-written file.
    """

    def __init__(self, dir: str | Path) -> None:
        self._dir = Path(dir)

    def save(self, run_id: str, snapshot: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / f"{run_id}.json"
        fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, ensure_ascii=False)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load(self, run_id: str) -> dict[str, Any] | None:
        target = self._dir / f"{run_id}.json"
        if not target.exists():
            return None
        with target.open(encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        return data
