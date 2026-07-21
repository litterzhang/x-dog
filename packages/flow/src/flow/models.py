"""flow.models — frozen dataclass domain model for workflow definitions.

Data flows between nodes through **named ports**, not a shared flat state.  Each
node declares ``input_ports`` and ``output_ports``; an :class:`EdgeDef` carries an
explicit ``mapping`` of ``(source_output_port, destination_input_port)`` pairs, so
``nodeA.output.x -> nodeB.input.a`` is spelled out rather than implied by shared
key names.  Initial workflow inputs are exposed as the output ports of a reserved
synthetic source node whose id is :data:`IN_NODE_ID`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Reserved id for the synthetic source node whose output ports carry the
# workflow's initial ``state`` values.  Chosen to be a non-identifier so it can
# never collide with a user-authored node id.
IN_NODE_ID = "$in"


@dataclass(frozen=True)
class Port:
    """A named, typed data port on a node.

    ``type`` is a JSON type name (``string``/``integer``/``number``/``boolean``/
    ``array``/``object``) used to coerce the string-valued wire format to/from the
    Python value a script sees.  Agent ports are almost always ``string``.
    """

    name: str
    type: str = "string"


@dataclass(frozen=True)
class NodeDef:
    id: str
    type: Literal["agent", "script"] = "agent"
    model: str | None = None
    system_prompt: str = ""
    prompt: str = ""
    tools: tuple[str, ...] = ()
    run: str | None = None
    # Data ports: each node reads its input ports and writes its output ports.
    input_ports: tuple[Port, ...] = ()
    output_ports: tuple[Port, ...] = ()
    # Structured-output agents: field -> JSON type; forces a submit_result call
    # whose validated JSON is stored in the node's single output port.
    output_schema: tuple[tuple[str, str], ...] = ()
    # Inline script source (agent nodes leave this empty).
    code: str | None = None
    # Agent nodes: enable the built-in web_search tool.  ``web_search_model``
    # optionally overrides which model performs the search (some models don't
    # browse — e.g. Claude on Copilot — so a browsing model like gpt-5.5 can be
    # named here); falls back to the node/default model when None.
    web_search: bool = False
    web_search_model: str | None = None

    @property
    def input_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.input_ports)

    @property
    def output_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.output_ports)


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
    # Explicit data mapping: (source_output_port, destination_input_port) pairs.
    # An empty mapping is a pure control edge (ordering only, no data moved).
    mapping: tuple[tuple[str, str], ...] = ()
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
    # Seed values exposed as the output ports of the reserved IN_NODE_ID source.
    initial_state: tuple[tuple[str, str], ...] = field(default=())
