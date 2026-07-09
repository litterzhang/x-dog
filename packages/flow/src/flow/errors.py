"""flow.errors — workflow exception hierarchy."""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for all flow workflow errors."""


class WorkflowValidationError(WorkflowError):
    """Raised when a workflow definition fails validation."""


class WorkflowExecutionError(WorkflowError):
    """Raised when a workflow fails during execution."""
