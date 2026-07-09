"""Prompt system — static base + workspace overrides + dynamic context."""
from claw.core.prompt.builder import build_system_prompt  # noqa: F401
from claw.core.prompt.workspace import (  # noqa: F401
    init_workspace,
    workspace_path,
    run_bootstrap,
)
