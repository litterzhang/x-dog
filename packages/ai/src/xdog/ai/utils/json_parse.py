"""Streaming JSON parser for incrementally received tool-call arguments.

When an LLM streams the ``arguments`` of a tool call as partial JSON
fragments, :func:`parse_partial_json` attempts to recover a usable Python
object at each step -- closing any unclosed braces/brackets/strings so the
fragment can be parsed by the stdlib ``json`` module.
"""

from __future__ import annotations

import json
import re
from typing import Any


def parse_partial_json(text: str) -> Any | None:
    """Parse *text* as JSON, auto-closing unclosed structures.

    Returns the parsed Python object, or ``None`` if the text cannot be
    meaningfully interpreted as JSON even after best-effort repairs.
    """
    if not text or not text.strip():
        return None

    cleaned = text.strip()

    # Fast path: text is already valid JSON.
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt progressive repairs.
    repaired = _repair(cleaned)
    if repaired is not None:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Internal repair heuristics
# ---------------------------------------------------------------------------

_OPEN_TO_CLOSE: dict[str, str] = {"{": "}", "[": "]"}


def _repair(text: str) -> str | None:
    """Try to close unclosed brackets / braces / strings in *text*."""
    # Remove trailing commas before we try closing.
    text = re.sub(r",\s*$", "", text)

    stack: list[str] = []
    in_string = False
    escape_next = False

    for ch in text:
        if escape_next:
            escape_next = False
            continue

        if ch == "\\":
            escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[ch])
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()

    # If we are still inside a string, close it.
    suffix = ""
    if in_string:
        suffix += '"'

    # Close any remaining open brackets/braces.
    suffix += "".join(reversed(stack))

    return text + suffix if suffix else text
