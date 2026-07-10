"""flow.interpolate — template interpolation for workflow state."""

from __future__ import annotations

import re
from collections.abc import Mapping

_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def interpolate(template: str, state: Mapping[str, str]) -> str:
    """Replace {{key}} occurrences with state[key]; missing keys become empty string."""
    return _PATTERN.sub(lambda m: state.get(m.group(1), ""), template)
