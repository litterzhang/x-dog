"""flow.models — frozen dataclass domain model for workflow definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NodeDef:
    id: str
    type: Literal["agent", "script"] = "agent"
    model: str | None = None
    system_prompt: str = ""
    prompt: str = ""
    output: str | None = None
    tools: tuple[str, ...] = ()
    run: str | None = None
    inputs: tuple[str, ...] = ()
    output_schema: tuple[tuple[str, str], ...] = ()
    # Script-node typing + inline code (agent nodes leave these empty).
    code: str | None = None
    input_schema: tuple[tuple[str, str], ...] = ()
    output_type: str | None = None


@dataclass(frozen=True)
class Condition:
    op: Literal["equals", "contains", "not", "and", "or"]
    value: str | None = None
    text: str | None = None
    children: tuple[Condition, ...] = ()


@dataclass(frozen=True)
class EdgeDef:
    src: str
    dst: str
    when: Condition | None = None
    loop_max: int | None = None


@dataclass(frozen=True)
class WorkflowDef:
    name: str
    provider: str
    entry: str
    nodes: tuple[NodeDef, ...]
    edges: tuple[EdgeDef, ...]
    default_model: str = ""
    initial_state: tuple[tuple[str, str], ...] = ()
