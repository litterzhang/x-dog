"""flow.models — frozen dataclass domain model for workflow definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NodeDef:
    id: str
    type: Literal["agent"] = "agent"
    model: str | None = None
    system_prompt: str = ""
    prompt: str = ""
    output: str | None = None


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
