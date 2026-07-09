"""Utility sub-package for ai.

Re-exports commonly used helpers so callers can do::

    from ai.utils import EventStream, parse_partial_json
"""

from ai.utils.event_stream import EventStream
from ai.utils.hash import sha256_hex, stable_hash
from ai.utils.json_parse import parse_partial_json
from ai.utils.overflow import estimate_token_count, is_context_overflow
from ai.utils.sanitize_unicode import sanitize_unicode
from ai.utils.validation import validate_tool_arguments

__all__ = [
    "EventStream",
    "estimate_token_count",
    "is_context_overflow",
    "parse_partial_json",
    "sanitize_unicode",
    "sha256_hex",
    "stable_hash",
    "validate_tool_arguments",
]
