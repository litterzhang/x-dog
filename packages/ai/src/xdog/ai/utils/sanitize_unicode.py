"""Unicode sanitization utilities.

LLM outputs and tool results may contain problematic Unicode characters
(control characters, zero-width spaces, bidirectional overrides, etc.).
This module provides a simple sanitizer that strips or replaces them.
"""

from __future__ import annotations

import re
import unicodedata

# Regex matching Unicode categories we want to strip:
#   Cc = control characters (except \n \r \t)
#   Cf = format characters (zero-width joiners, BOM, bidi overrides)
#   Co = private use area
# We keep newline (\n), carriage return (\r), and tab (\t).
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"  # C0 controls minus \t \n \r
    r"\u200b-\u200f"  # zero-width spaces & bidi marks
    r"\u2028\u2029"  # line/paragraph separator
    r"\u202a-\u202e"  # bidi embedding/override
    r"\u2060-\u2064"  # invisible operators
    r"\ufeff"  # BOM / zero-width no-break space
    r"\ufff9-\ufffb"  # interlinear annotations
    r"]"
)

# Surrogate halves that may appear in incorrectly encoded strings.
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def sanitize_unicode(text: str, *, replacement: str = "") -> str:
    """Remove or replace problematic Unicode characters from *text*.

    Parameters
    ----------
    text:
        The input string.
    replacement:
        The replacement for each removed character.  Defaults to the
        empty string (i.e. the characters are stripped).

    Returns
    -------
    str
        The sanitized string.
    """
    result = _CONTROL_RE.sub(replacement, text)
    result = _SURROGATE_RE.sub(replacement, result)
    # Normalize to NFC so combining characters are composed.
    result = unicodedata.normalize("NFC", result)
    return result
