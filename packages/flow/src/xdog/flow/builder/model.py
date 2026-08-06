"""flow.builder.model — headless builder state.

``BuilderModel`` is a frozen snapshot of the workflow being edited plus a little
UI state (which node is selected, whether there are unsaved changes, and the
last validation error).  It is deliberately terminal-free so the whole builder
can be unit-tested by applying :mod:`flow.builder.actions` and asserting on the
resulting model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from xdog.flow.errors import WorkflowValidationError
from xdog.flow.loader import validate_workflow
from xdog.flow.models import WorkflowDef


@dataclass(frozen=True)
class BuilderModel:
    """Immutable builder state: the workflow plus selection + status."""

    wf: WorkflowDef
    selected: str | None = None
    dirty: bool = False
    error: str | None = field(default=None)

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(n.id for n in self.wf.nodes)

    def with_wf(self, wf: WorkflowDef, *, dirty: bool = True) -> BuilderModel:
        """Return a new model with *wf* installed, re-validated, marked dirty.

        The selection is preserved when the selected node still exists, else it
        falls back to the first node (or ``None`` for an empty workflow).
        """
        selected = self.selected
        ids = {n.id for n in wf.nodes}
        if selected not in ids:
            selected = wf.nodes[0].id if wf.nodes else None
        return replace(self, wf=wf, selected=selected, dirty=dirty, error=_validation_error(wf))


def _validation_error(wf: WorkflowDef) -> str | None:
    """Return the validation error message for *wf*, or ``None`` if valid."""
    try:
        validate_workflow(wf)
    except WorkflowValidationError as exc:
        return str(exc)
    return None


def empty_model(name: str = "untitled", provider: str = "copilot") -> BuilderModel:
    """Return a builder model for a brand-new, empty workflow."""
    wf = WorkflowDef(name=name, provider=provider, entry="", nodes=(), edges=())
    return BuilderModel(wf=wf, selected=None, dirty=False, error=_validation_error(wf))


def model_from_workflow(wf: WorkflowDef) -> BuilderModel:
    """Return a builder model wrapping an existing (loaded) workflow."""
    selected = wf.nodes[0].id if wf.nodes else None
    return BuilderModel(wf=wf, selected=selected, dirty=False, error=_validation_error(wf))
