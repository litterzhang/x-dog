"""flow — multi-agent workflow engine and code generator."""

from __future__ import annotations

from xdog.flow.builder.serialize import dump_workflow, workflow_to_dict
from xdog.flow.builder.svg_doc import dump_workflow_svg, read_workflow_from_svg, workflow_to_svg_document
from xdog.flow.codegen import generate
from xdog.flow.executor import execute
from xdog.flow.graph import to_ascii, to_mermaid, to_svg
from xdog.flow.loader import load_workflow
from xdog.flow.models import WorkflowDef
from xdog.flow.telemetry import MetricsCollector, NodeMetrics, RunMetrics

__all__ = [
    "WorkflowDef",
    "load_workflow",
    "execute",
    "generate",
    "workflow_to_dict",
    "dump_workflow",
    "to_ascii",
    "to_mermaid",
    "to_svg",
    "workflow_to_svg_document",
    "read_workflow_from_svg",
    "dump_workflow_svg",
    "MetricsCollector",
    "NodeMetrics",
    "RunMetrics",
]
