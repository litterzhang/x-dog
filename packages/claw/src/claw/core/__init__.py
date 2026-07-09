"""Core claw runtime.

Importing this package loads all domains, which registers their tools
with the tool registry. The boot order is:

1. ``tools/``    — registers agent builtins (filesystem, bash, current_time, todo)
2. ``runtime/``  — registers group_message tool
3. ``planning/`` — registers goal + task tools
4. ``memory/``   — registers memory tool
5. ``skills/``   — registers skill tool
"""
import claw.core.tools      # noqa: F401 — agent builtins
import claw.core.runtime    # noqa: F401 — group_message
import claw.core.planning   # noqa: F401 — goal + task
import claw.core.memory     # noqa: F401 — memory
import claw.core.skills     # noqa: F401 — skills
