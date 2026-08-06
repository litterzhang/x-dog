"""Environment section — date, platform, model info."""
from __future__ import annotations

import platform
from datetime import date

from xdog.claw.core.prompt.templates import ENVIRONMENT_SECTION


def environment_section(model: str = "") -> str:
    """Build the environment context section."""
    return ENVIRONMENT_SECTION.format(
        date=date.today().isoformat(),
        platform=platform.system().lower(),
        model=model or "unknown",
    )
