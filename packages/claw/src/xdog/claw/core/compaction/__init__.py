"""Compaction package — transcript compaction, memory flush, and summarization.

Public API:
- ``should_compact()`` — trigger check
- ``run_compaction()`` — full pipeline
- ``estimate_tokens()`` — rough token count
- ``compact_transcript()`` — pure data manipulation
- ``archive_transcript()`` — save to markdown
"""
from xdog.claw.core.compaction.pipeline import run_compaction  # noqa: F401
from xdog.claw.core.compaction.prompts import should_compact  # noqa: F401
from xdog.claw.core.compaction.transcript import (  # noqa: F401
    archive_transcript,
    compact_transcript,
    estimate_tokens,
)


# Explicit re-export: without `__all__` a strict checker treats these as
# private to this module and rejects every import of them elsewhere.
__all__ = [
    "archive_transcript",
    "compact_transcript",
    "estimate_tokens",
    "run_compaction",
    "should_compact",
]
