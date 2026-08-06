"""flow.builder.svg_doc — embed/extract a workflow inside an SVG document.

The SVG is dual-purpose: a rendered diagram that also carries the full workflow
JSON as element text, so the builder can reload and edit it.  The embedded JSON
(``workflow_to_dict(wf)``) is the single source of truth; the drawing is derived
and never read back.  The round-trip law::

    parse_workflow(read_workflow_from_svg(workflow_to_svg_document(wf))) == wf
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from xdog.flow.builder.serialize import workflow_to_dict
from xdog.flow.errors import WorkflowValidationError
from xdog.flow.graph import to_svg
from xdog.flow.models import WorkflowDef

_SVG_NS = "http://www.w3.org/2000/svg"
_MARKER_ID = "flow-workflow"


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def workflow_to_svg_document(wf: WorkflowDef) -> str:
    """Render *wf* to an SVG string carrying the workflow JSON as a marker."""
    ET.register_namespace("", _SVG_NS)
    root = ET.fromstring(to_svg(wf))
    marker = ET.Element(f"{{{_SVG_NS}}}metadata")
    marker.set("id", _MARKER_ID)
    marker.set("data-flow", "workflow")
    marker.text = json.dumps(workflow_to_dict(wf), ensure_ascii=False)
    root.insert(0, marker)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>' + body


def read_workflow_from_svg(source: str | Path) -> dict[str, Any]:
    """Return the embedded workflow dict from an SVG path or string."""
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
    elif "<" in source:
        text = source
    else:
        text = Path(source).read_text(encoding="utf-8")

    root = ET.fromstring(text)
    for el in root.iter():
        if _localname(el.tag) == "metadata" and el.get("id") == _MARKER_ID:
            payload = el.text
            if payload is None or not payload.strip():
                raise WorkflowValidationError("Empty flow-workflow marker in SVG")
            result: dict[str, Any] = json.loads(payload)
            return result
    raise WorkflowValidationError("No flow-workflow marker found in SVG")


def dump_workflow_svg(wf: WorkflowDef, path: str | Path) -> None:
    """Write the dual-purpose SVG document for *wf* to *path* as UTF-8."""
    Path(path).write_text(workflow_to_svg_document(wf), encoding="utf-8")
