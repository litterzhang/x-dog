"""flow.scheduler — install and run scheduled workflows (docs/scheduling.md).

The scheduler wraps a built ``--portable`` bundle; it never changes execution.
``systemd`` renders unit files; ``listener`` is the shared hook listener; the
``xdog-flow install`` command wires them together.
"""

from __future__ import annotations
