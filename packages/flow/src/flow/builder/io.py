"""flow.builder.io — format dispatch for loading/saving workflows.

The builder can persist a workflow as plain ``.json`` or as an ``.svg`` document
that embeds the workflow JSON (a rendered diagram that is also its own source —
see :mod:`flow.builder.svg_doc`).  These helpers pick the right (de)serializer
from the path suffix so the rest of the builder stays format-agnostic.
"""

from __future__ import annotations

from pathlib import Path

from flow.builder.serialize import dump_workflow
from flow.builder.svg_doc import dump_workflow_svg, read_workflow_from_svg
from flow.loader import load_workflow, parse_workflow, validate_workflow
from flow.models import WorkflowDef


def _is_svg(path: str | Path) -> bool:
    return Path(path).suffix.lower() == ".svg"


def load_any(path: str | Path) -> WorkflowDef:
    """Load a workflow from *path*, dispatching on ``.svg`` vs ``.json``.

    ``.svg`` files are read via their embedded JSON (the drawing is derived);
    everything else goes through the plain JSON loader.  The result is validated
    either way.
    """
    if _is_svg(path):
        wf = parse_workflow(read_workflow_from_svg(Path(path)))
        validate_workflow(wf)
        return wf
    return load_workflow(path)


def dump_any(wf: WorkflowDef, path: str | Path) -> None:
    """Save *wf* to *path*, writing an SVG document for ``.svg`` else JSON."""
    if _is_svg(path):
        dump_workflow_svg(wf, path)
    else:
        dump_workflow(wf, path)
