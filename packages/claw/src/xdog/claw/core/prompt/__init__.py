"""Prompt system — static base + workspace overrides + dynamic context."""
from xdog.claw.core.prompt.builder import build_system_prompt  # noqa: F401
from xdog.claw.core.prompt.workspace import (  # noqa: F401
    init_workspace,
    run_bootstrap,
    set_identity_name,
    workspace_path,
)

# Explicit re-export: without `__all__` a strict checker treats these as
# private to this module and rejects every import of them elsewhere.
__all__ = [
    "build_system_prompt",
    "init_workspace",
    "run_bootstrap",
    "set_identity_name",
    "workspace_path",
]
