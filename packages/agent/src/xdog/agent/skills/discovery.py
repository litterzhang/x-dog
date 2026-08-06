"""Discovering skills that ship inside installed packages.

A distribution can carry a skill by putting a ``skill/SKILL.md`` inside its
package directory — ``xdog-flow`` does, teaching an agent to write flow
workflows. Nothing has to be registered: this walks the ``xdog`` namespace and
picks up whatever is installed, so ``pip install xdog-flow`` makes the skill
appear and ``pip uninstall`` makes it go away.

That matters more than it sounds. The alternative — copying SKILL.md into a
user-level skills directory — was how this was done before, and the copy
silently drifted from the package it documented, teaching the agent an API that
had already changed underneath it. Reading it out of the installed package is
the only version that cannot go stale.
"""
from __future__ import annotations

import logging
import pkgutil
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger(__name__)

#: Directory name a package uses to ship its skill.
SKILL_DIRNAME = "skill"


def packaged_skills() -> dict[str, Path]:
    """Map slug → skill directory for every installed ``xdog.*`` package.

    The slug is the subpackage name, so ``xdog.flow``'s skill is ``flow`` — not
    ``skill``, which is what the directory is actually called and which would
    collide the moment a second package shipped one.

    Never raises: a package that fails to introspect is skipped with a log
    line. Skill discovery is a convenience, and it must not be able to stop an
    agent from starting.
    """
    found: dict[str, Path] = {}
    try:
        import xdog
    except ImportError:  # pragma: no cover - xdog is this package's own namespace
        return found

    for module in pkgutil.iter_modules(xdog.__path__, "xdog."):
        slug = module.name.rpartition(".")[2]
        try:
            candidate = Path(str(files(module.name))) / SKILL_DIRNAME
            if (candidate / "SKILL.md").is_file():
                found[slug] = candidate
        except (ImportError, TypeError, OSError, ModuleNotFoundError) as exc:
            logger.debug("skipping %s while looking for skills: %s", module.name, exc)

    return found
